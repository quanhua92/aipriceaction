# Implementation plan

This is the current roadmap for the FastAPI replacement. Checkboxes certify
only the stated checks, not full data coverage or production readiness. Earlier
staging narratives and superseded checklists remain in `VALIDATION.md` and Git
history through `eb814ab`; they are not additional current open tasks.

## Historical verified checkpoint — 2026-10-05 ICT

This is the October 5 operational/data checkpoint, not the current test count.
The latest endpoint implementation and validation status is recorded below.

- [x] Expand operational ingestion beyond the legacy 404-symbol Vietnamese
  catalog. The packaged exchange directory contains 1,544 stock identities
  (403 HOSE, 302 HNX and 839 UPCoM). Compose dedicates its single worker to VN,
  retains three daily years for the complete directory, and promotes the top
  80% of recently active stocks by average traded value to minute/hourly
  ingestion after at least 80% of daily discovery is ready. This replaces a
  subjective blacklist with a reproducible activity and liquidity rule.

- The default Compose stack now builds and runs the Python FastAPI service,
  Python worker, one-shot initializer and RustFS. SQLite/catalog/cache data is a
  host bind mount; no legacy Rust API service is present.
- Compose initialization refreshes the authoritative live ticker groups. The
  isolated acceptance run matched the live API byte-for-byte for 404 VN, 60
  crypto and 134 Yahoo-mode symbols, registering all 598 distinct source
  identities. Catalog mode scheduled 1,528 source/interval bootstrap jobs.
- A catalog-sized worker exposed and then passed a SQLite scheduler-depth fix.
  The rebuilt container remained healthy and ingested 500 recent AAA rows for
  each of daily, hourly and minute intervals. FastAPI served the resulting row,
  every mounted legacy route, and the complete explorer/static bundle.

- FastAPI, SQLite retention, Parquet/DuckDB history, three preferred VN adapters plus opt-in VCI minute fallback,
  workers, operational CLI and web/SDK interfaces are implemented locally.
- The scoped VN/crypto/global workers advance the live database; populated
  backup images and their exact scope are recorded in `VALIDATION.md`.
  The latest verified S3 index has 887 active objects, 79 handoff certificates, 34 recoveries and
  eight invalid-date/basis markers preserving VND and older VNINDEX observations.
- There are 58 VN minute handoffs and four Yahoo minute handoffs (AAPL, SPY,
  S&P and Dow). VNINDEX, MSFT/NVDA and gold minute snapshots remain frozen.
  SPY also has a verified native hourly handoff; five other current stock/index
  hourly snapshots remain frozen. Gold hourly history has a verified native
  handoff pinned to the legacy five-day request policy, with all 279 legacy
  quote events preserved explicitly and `1h` enabled in its watchlist entry.
  Gold minute history is also current as a public snapshot, with all 241 legacy
  quote-event seconds preserved; native gold minute ingestion remains frozen.
- Eleven licensed VN stock minute series now use VCI: FPT, TPB, BSR, CTG, GAS,
  GEE, SHB, TCB, VHM, VND and VCB. The three preferred providers remain unchanged for other sources/windows.
- DNSE's captured VN30 daily timestamp transition is supported without altering
  native OHLCV. Fresh index comparisons still show source price/volume
  disagreements; VCI daily/minute coherence is verified only on the recorded
  controls, not a license to replace unresolved index history.
- The latest complete API suite passes 1,115 tests; lint/format checks pass.
  Real worker, HTTP, SDK, public-web and populated restoration evidence is
  recorded with its scope and limits in `VALIDATION.md`.
- Actual scoped Git commits include `c037c53` (storage), `23fc706` (workers/CLI),
  `b8d74b5` (API), `0a3724f` (SDK), `27fe152` (daily handoffs), `44cba6a`
  (browser selection), `5f6b93d` (AAPL/SPY updates) and `eb814ab` (Yahoo restart
  overlap). Proposed commit labels below describe implementation units; they
  do not imply those exact titles were all committed separately.

## Remaining acceptance gates

- [x] Fix hourly publication verification storage: use TemporaryDirectory for
  the post-change full SQLite copy, preserve the operational rollback before-image,
  and retain only verification hashes/counts. Clean up successful/failed copies
  and sidecars. Filter expired staging with the publisher's effective retention
  floor; empty batches must not create backups or provider clients. Nine new
  storage tests and 17 focused checks pass, including actual small-fixture
  publication. The live preview has no eligible prefixes and retains only
  174 bytes. Keep the four missing-session hourly jobs pending.

- [x] Query the five exact missing stock/hourly date pairs for the four pending
  jobs against VPS, VNDirect, DNSE and the live legacy API. Recover 15 raw-bound
  legacy hourly bars for IDC/VGI/VTP; all three native feeds lack these dates.
  GEE's two dates have no usable legacy rows. Preserve source errors and daily
  price/volume disagreements; no missing-date exclusions or publication occur.
  Add an offline candidate reviewer with exact temporary SQLite/Parquet round
  trips, existing-row refusal and cleanup tests. Retain 89,516 bytes of evidence;
  37 focused tests and lint pass. All four hourly jobs remain pending.

- [x] Fresh-check PNJ's October 2 minute exceptions against all four native
  providers and replay each captured response. All 226 observed timestamps and
  each provider's prior values remain stable. SQLite matches fresh VPS exactly;
  VNDirect/DNSE/VCI still disagree at individual minutes. Preserve the full 70-row
  disagreement matrix and source hashes in 532,408 bytes without correcting by
  consensus or equal day totals. Defer identical PNJ probes until new evidence;
  minute-level truth and the broader exception gates remain unresolved.

- [x] Reparse all 169 successful native daily controls in the saved selected
  universe through the current runtime adapters; verify recorded request source,
  symbol, interval, date window and capture hashes. All 124,764 rows match exactly.
  Keep eight rejected full responses excluded and preserve the separate malformed
  subset evidence. Add `--replay-native-daily` to compact universe diagnostics,
  retaining hashes/counts rather than duplicate candles or database copies.
- [x] Complete the integrated 59-series audit with successful native control
  replay plus rejected-response valid subsets. All source record hashes and
  per-symbol price/coherence summaries match the previous checkpoint. Retain
  2,376,040 bytes of replay receipts; close SQLite and verify the WAL disappears.
  The same eight incomplete VCI traversals and other data disputes remain open.

- [x] Check recent official HNX scale totals against dated native controls and
  preserve bounded, offline replayable query evidence. Adjacent SHS sessions
  match VNDirect totals; one official component sum differs by 1,800 shares.
  The attempted April 22 query returns no records. Keep board/session scope
  unresolved and do not infer odd-lot corrections or license minute allocation.
  Diagnostic parser rejects changed source/query/layout and has 12 focused tests.

- [x] Propose the newly discovered MWG VCI minute correction from complete
  cumulative-volume captures and verify it against fresh VNDirect/DNSE daily
  witnesses. Replay the same page: active catalog rejects; candidate validates
  10,000 rows with one scoped proof. A candidate-only full-year VCI probe returns
  55,262 candles across 247 observed dates. Keep VNINDEX's failed proposal blocked.
  Preserve small captures/reports; no database copies or canonical changes.
- [x] Rehearse a volume-only candidate for MWG Unix 1780382460 (700 to
  800), preserving legacy OHLC prices, original provenance and archive rollback.
  Do not adopt VCI's different adjusted price basis through a volume license.
  The complete 225-row legacy day has the same source timestamps/volumes and
  reconciles to the two daily witnesses after one correction. Original/staged
  Parquet round trips preserve all fields; temporary objects and fixture SQLite
  are removed on success/failure. Retain only the small rehearsal report.
- [x] Add guarded volume-only publication with persisted correction
  provenance, immutable original evidence, race/lease checks and replayable
  archive-index restoration. Keep raw legacy candles immutable; apply replayed
  receipts in History before aggregation so handoff checks preserve their frozen
  originals. Freeze source captures and original-day Parquet; reject changed
  snapshots/targets and conflicting receipts. Pass five receipt tests plus the
  925-test full suite. The real MWG preview passes without publication.
- [x] Restart all local API/worker processes on receipt-aware code before local
  MWG activation; older manifest writers would omit the receipt metadata.
  Publish the verified receipt, check live API aggregates and isolated RustFS
  restore, and remove all temporary restore databases/objects afterward.
  Local API/VN/Yahoo/crypto PIDs are 98057/98058/98059/98060. The first attempt
  correctly refused a busy MWG worker lease; retry published one receipt.
  Verify 225 minute/16 fifteen-minute candles, one changed volume per interval,
  unchanged OHLC and the exact 2,969,500 day total. Replay all 888 archive objects
  and the receipt in a temporary index; mount only original-day evidence for
  the scoped cold read. No full hot SQLite recovery is claimed. Verify the latest
  worker-written manifest retains the receipt; keep the 4 KB restore report.
  Align automated comparisons with the API's receipt-aware data; 26 focused tests pass.

- [x] Automatically summarize observed dates and candle counts per date for
  every compared feed, exposing missing days and partial sessions independently
  of cursor completion. Replay the existing full-year controls without downloads
  or database copies: FPT has 247 observed dates on VCI versus 127 on VNDirect,
  62 on DNSE and five on VPS. VNDirect lacks 120 dates in that observed union;
  this is not an exchange-calendar completeness claim. Captured DNSE nextTime
  hints for FPT/MWG lead to null arrays rather than candles in read-only controls,
  so keep the strict adapter unchanged. Pass 185 provider/comparison/pipeline tests.

- [x] Add bounded native-feed pagination and atomic resumable feed-window
  checkpoints, bound to requested symbols/dates and the effective volume-proof
  catalog hash. Preserve completed errors on resume. Report empty/error/page-budget
  stops as incomplete history even when overlapping observations agree.
  Pass 19 focused tests and run October 4, 2025–October 2, 2026 minute controls
  for FPT/MWG/VNINDEX. VCI reaches the requested boundary for FPT; VNDirect
  reaches it for all three, which proves cursor progress rather than complete
  market coverage. Eight windows remain incomplete and five provider errors
  expose DNSE parsing/OHLC failures and unverified MWG/VNINDEX VCI volumes.
  Retain `data/paginated-minute-year-controls-20261004/` captures, comparisons
  and SQLite exception reports (about 82 MB); no database copies or publication.
  Resume repeats no provider calls; RustFS data remains 2.584 GB.
- [x] Add a shared artifact-byte budget to native/legacy captures, normalized
  feed checkpoints and comparison reports, including existing resume files and
  atomic replacement peak bytes. Cancel/close collectors on exhaustion and keep
  completed records. Add a resumable universe runner with at most four symbols
  in memory per batch, a default 2 GiB cap and reserved status-report space.
  Pass 25 focused budget/pagination/comparison/pipeline tests.
- [x] Complete the full-year minute universe comparison across all 59
  selected symbols under the 2 GiB cap. Artifacts/checkpoints are under
  `data/vn-minute-year-universe-20261004`; the run uses verified candidate VCI
  proofs and makes no canonical writes. All 236 windows finish explicitly;
  152 do not reach the boundary and 93 contain errors. Capture 51,253 shared
  four-feed agreements, 3,157 value disagreements and 2,174,108 incomplete
  observations. Evidence uses 1,503,844,875 bytes, with no database copies.
- [x] Review all 59 symbols against receipt-aware SQLite with bounded samples
  and complete timestamp counts. No conflicts/missing timestamps against
  unanimous providers; preserve 220 SQLite/provider difference categories.
  Complete calendar review: VPS observes five dates for every symbol, VNDirect
  127, DNSE 62 for stocks/89 for indices; 25 VCI series observe all 247 reference
  dates. No unexpected dates; date presence is not full-minute accuracy proof.
- [ ] Resolve the 34 VCI cumulative-volume refusals automatically. Offline
  replay of full-universe captures proposes 30 proofs on 26 symbols and blocks
  11 observations under existing rules; one GAS proposal predates the requested
  year. Keep the candidate catalog separate from active workers and recheck fresh
  daily witnesses before retrying the failed minute windows. Diagnose missing/bad
  source rows automatically; preserve strict ingestion and require evidence
  before licensing corrections.
- [x] Refresh all 29 in-window proposals with 58 exact-date VNDirect/DNSE
  requests; all pass. Recheck discovery failures with fresh exact-date witnesses,
  recovering VNM/GEX while preserving eight rejected observations on seven
  symbols and excluding the old GAS/GEE targets. Candidate catalog has 54 proofs;
  active worker catalogs/canonical data are unchanged. Replaying all rejected
  captures offline passes 27 symbol records and blocks VNINDEX/VN30/CEO/TCH/IDC/
  SHS/VGI. Pass 48 refresh/replay/proof tests, including tampered capture, wrong
  witness identity/volume and refusal to infer full requested-history coverage.
- [x] Continue the 27 replayed VCI records past their captured cursors under
  a 256 MiB artifact budget. Re-fetch and exactly compare the last captured page
  before continuing: all 27 joins match timestamps, OHLCV and cursor. Twelve
  candidates reach the requested boundary; 15 encounter further cumulative-volume
  contradictions. Preserve 286,067 additional valid candles through raw captures
  and compact checkpoints using 52,058,271 bytes; no database copies or publication.
  Pass 61 continuation/replay/budget/proof/refresh tests, including changed joins,
  out-of-window/non-progressing cursors and cleanup/checkpoint on budget exhaustion.
- [x] Automatically verify fresh daily witnesses for the newly exposed older
  contradictions and replay the combined captures without unnecessary downloads.
  Verify 16 additional proofs using 34 fresh daily requests; HPG remains blocked,
  and five older targets are outside the minute-year window. Candidate catalog
  has 70 proofs. Recheck fresh joins offline before combining capture references,
  enforcing hashes, page counts, consecutive request cursors and exact row counts.
  Review all 59 symbols: 2,552,713 accepted candles, 40 boundary-complete candidates
  observing all 247 reference dates, eight refusals and eleven passing partial
  candidates. No unexpected dates or unanimous-provider/SQLite conflicts; retain
  55,718 VCI/SQLite price differences and 192 volume differences. Pass 75 focused
  tests; retain only small reports/catalogs/source references, with no DB copies.
- [x] Continue the eleven passing partial candidates from the combined captured
  cursors, reusing source references and skipping 40 reviewed complete boundaries.
  Reject changed proof catalogs, incomplete/duplicate/scope-mismatched base reviews
  and a continuation bound to a different review checksum. Six candidates reach
  the boundary; five expose older contradictions. Preserve 58,766 additional rows
  through 17,313,438 bytes of evidence under a 128 MiB cap. Verify eight additional
  corrections with 16 daily requests; exclude one older MSN target. Candidate
  catalog has 78 proofs. Full combined review accepts 2,655,668 candles including
  partials, with 47 candidates observing all 247 reference dates and no unexpected
  dates. Keep eight refusals explicit; VCB/ACB/VPB/LPB need uncaptured older pages.
  Preserve 65,718 shared VCI/SQLite price differences and 200 volume differences;
  no unanimous-provider conflicts/missing timestamps. Pass 80 focused tests.
- [x] Continue VCB/ACB/VPB/LPB from the latest combined references, skipping 47
  complete windows under a 64 MiB cap. All four joins match and reach the boundary;
  no new proof requests are needed. Retain 43,938 additional candles through
  7,064,715 bytes of captures/reports. Final full-watchlist replay accepts 2,699,606
  candles including partials: 51 candidates contain all 247 reference dates,
  no passing partial windows remain and the eight unsupported refusals persist.
  Keep 81,274 shared VCI/SQLite price differences and 200 volume differences;
  no unanimous-provider conflicts/missing timestamps or unexpected dates.
- [x] Automatically audit the 78-proof candidate catalog against exact frozen
  legacy days and ready SQLite revisions. Find 55 available single-volume
  projections and one already-applied receipt; retain 21 refusals and one target
  outside the requested year. Eighteen refusals differ in frozen identity/volumes;
  three require unsupported peer/multiple projection semantics. Preserve all
  legacy prices/provenance. Keep a 45,073-byte report, no database copies, with
  explicit absence of publication license. Pass 100 focused tests including
  read-only projection/basis checks and correct recognition of an applied receipt.
- [x] Publish all 55 available single-volume projections atomically with immutable
  native captures/original-day Parquet, matching source/snapshot evidence, ready
  revisions and whole-batch lease/race guards. Refuse duplicate targets/days;
  publish one manifest rather than 55 snapshots. Pointer failure leaves the whole
  verified batch locally visible and permits safe retry. Keep raw candles and all
  prices/provenance unchanged. Pass the 991-test full suite before local activation.
  Verify all 11,706 minute/880 fifteen-minute candles across 55 days/27 symbols,
  exactly 55 changed volumes per interval, and full manifest/scoped cold restoration.
  Remove the temporary index/cache. Latest manifest includes all 56 receipts;
  post-activation projection audit recognizes 56 already applied, with prior
  refusals/exclusion unchanged. Full source replay reduces shared VCI/SQLite volume
  disagreements from 200 to 145; 81,274 price disagreements remain unchanged.
- [x] Automatically classify all remaining VCI/SQLite disagreements without
  changing strict comparisons or publication guards. Of 81,274 price differences,
  81,233 have uniform OHLC ratios (39,736 VCB and 41,497 MWG); 41 are nonuniform
  differences in September 28–October 2. None is two-decimal/integer rounding.
  All 145 volume differences are in those same five days across 38 ticker/date
  pairs with identical observed timestamps and equal day totals. Preserve both
  sides' counts/totals and bounded examples, with no source winner or inferred
  dividend. Reconcile classification/band/day counts against exact comparisons;
  pass 24 focused tests. Keep 1,143,308 bytes of reports/references, no DB copies.
- [x] Review September 28–October 2 across all 59 selected symbols and all four
  native providers, including six provider pairs, automatically from saved
  observations. Replay combined checksummed VCI captures with their exact proof
  catalog when original accepted pages do not reach this window. Five supplements
  provide observations; VN30's rejection remains explicit. Preserve original
  errors, missing timestamps, source hashes and read-only snapshot limitations.
  VCI has 41 price/145 volume differences; all 38 differing-volume days have equal
  observed day totals and matching times. Other providers also disagree, so no
  winner is selected. Pass 31 focused tests; retain one 2,365,323-byte report and
  remove the superseded exploratory report. No DB copies or canonical/S3 writes.
- [x] Automatically trace every uniform-price-ratio exception in the completed
  VCI review, replaying its exact native captures/proof catalog and comparing each
  observed session with retained daily and saved four-feed daily observations.
  VCB has 39,736 scaled candles, a mixed June 23 session (166 scaled/60 equal),
  then equality from June 24. MWG has 41,497 scaled candles, a mixed July 3 session
  (100 scaled/125 equal), then equality apart from one recent candle. Both VCI
  minute series align with SQLite/VPS daily prices within one VND on all 247
  observed dates and VNDirect within half a VND. Preserve legacy/DNSE differences;
  do not infer dividend causes or apply ratios. All timestamps match. VCB has
  two exact native daily-volume witnesses on 247 dates; MWG on 246, with its known
  November 10, 2025 residual still unresolved. Pass 20 focused tests and retain
  one 2,029,622-byte report without SQLite copies or S3 changes.
- [x] Extend eligible replacement captures to the complete existing archive
  boundary without dropping older API timestamps. Automatically select VCB and
  keep MWG's unresolved witness exception refused. Reparse original captures
  at September 3, 2025 with exact old-observation preservation; verify a fresh
  10,000-candle seam and fetch one older page. VCB covers all 60,747 observations.
  Preserve 1,444,286 bytes of source captures/report references; no DB/S3 writes.
- [x] Rehearse a coherent VCB replacement from the verified capture chain,
  replaying native daily witnesses and fresh source joins before publication.
  Replay all three native daily captures and check all 270 observed sessions;
  retain price agreement within one VND and two exact native daily-volume witnesses
  on every date. Preserve all 60,747 timestamps (55,556 hot/5,191 cold); compare
  18 minute/15m/minute-derived-hour raw/SMA/EMA boundary queries and exact
  archive-index/hot-image restoration. Remove all temporary databases/caches and
  eight owned RustFS objects, verify no current objects/versions/delete markers,
  retain 54,850 bytes of reports. Pass 50 focused tests including failure cleanup.
- [x] Verify fresh oldest/middle/latest source controls and license VCB's live
  handoff through guarded local activation. Fresh 1,000/1,000/2,000-candle controls
  match exactly. Publish all 60,747 observations without dropping timestamps,
  replay native daily witnesses, verify a 40-row worker refresh, 18 boundary
  queries and six actual API route cases. Restart the API/VN worker with the
  active 27-proof catalog, then verify six live HTTP cases and the entire VCB
  series through temporary restoration of all 887 archive-index objects.
- [x] Verify the latest populated operational rollback and retire its superseded
  full SQLite predecessor. Check original VCB state/hot checksum/archive metadata
  and preserved published SHB state; remove only the old 911,646,720-byte backup.
  Preserve active catalogs/captures, immutable S3 before-images, unresolved
  candidates and the latest full rollback. All verifier databases/caches are
  temporary and removed on success/failure; local data measures 7,620,676 KiB.
- [ ] Resolve MWG's exact minute-volume attribution for November 10, 2025 and
  the separately recorded older September 16 residual before snapshot adoption.
- [x] Add a compact automated minute/daily coherence review for every saved
  symbol, including incomplete VCI captures. Compare both receipt-aware current
  SQLite minutes and replayed VCI minutes against all saved daily witnesses;
  count every observed date and retain at most 20 exception examples per category.
  Exclude legacy from native witness counts; keep original source errors and
  incomplete capture status. Reject compact diagnostics as replacement-extension
  inputs. Pass the 1,046-test full suite without database copies or new feeds.
- [x] Complete all 59 minute/daily coherence reviews from immutable source
  captures, in one read-only SQLite snapshot without provider requests or database
  copies. Replay 2,699,606 accepted native minutes; retain eight source refusals.
  VCB's 55,556 retained-year observations now match exactly. Remaining price
  classifications include 41,497 uniform differences (MWG) and 41 nonuniform
  differences across 19 symbols. Twenty-two current series have two saved native
  daily price/volume witnesses on all 247 observed dates under the stated exact
  thresholds; this does not prove each minute allocation or market completeness.
  Retain a 2,593,363-byte report under the 8 MiB cap. RustFS data remains
  2,701,576 KiB; local data measures 7,623,232 KiB, with no test databases.
- [x] Check DNSE's official historical-trade interface for a possible MWG repair
  source. Preserve 14,732 bytes of public schema/report evidence under a 32 KiB
  cap. The documented route requires API key/date/signature headers; no signed
  trade request or canonical change occurs. No public recovery route is verified.
- [x] Add opt-in daily valid-subset replay to the compact universe review.
  Bind the diagnostic report to its original audit, verify capture bytes and
  reparse each failed native response through the unchanged runtime parser.
  Require exact saved row/rejection identity; preserve full-response errors and
  exclude malformed dates. Reparse eight native subsets: 5,937 valid observations
  and 13 rejected observations. Legacy never supplies a native witness. Restrict
  this path to compact diagnostics; no ingestion or publication rule is relaxed.
  Pass 27 focused tests and the 1,053-test complete suite.
- [x] Complete all 59 coherence reviews with eight verified native daily
  subsets. Existing canonical minutes gain two saved native daily-price controls
  on 1,011 stock/date pairs and exact-volume controls on 1,036 pairs. Twenty-three
  series now meet both controls on all 247 dates; GEX joins the previous 22.
  Native VCI price classifications and eight incomplete traversals remain unchanged.
  Preserve the 2,181,230-byte report and a 5,891-byte comparison receipt; no provider
  request, database copy, active-catalog change or canonical correction occurs.
- [x] Explicitly close the read-only SQLite snapshot on success/failure and test
  both paths. The live WAL grows transiently during concurrent worker writes,
  then disappears after the completed audit; do not delete live sidecars manually.
  Pass 31 focused tests and the 1,057-test full suite after the lifecycle fix.
- [x] Distinguish SHS's formerly unavailable VNDirect witness from its actual
  April 22, 2026 volume disagreement: VNDirect 8,071,900 versus DNSE 8,067,500.
  The cumulative-volume proof still refuses the correction; preserve a bounded
  1,088-byte target report and source hashes without licensing publication.
- [ ] Follow up native daily-source errors, representation differences and the
  remaining minute exceptions using complete evidence before any publication.
  Reuse diagnostic valid subsets where a full native page was rejected; distinguish
  unavailable witnesses from actual canonical-data disagreement.
- [ ] Resolve the remaining MWG historical price basis and other source disagreements. Investigate
  the pre-VCB-activation 41 recent OHLC differences and 145 minute-volume allocations across all
  native providers before provider adoption or price changes. Equal day totals
  cannot license changing individual minute values. Reference-date presence does
  not certify every minute or OHLCV accuracy; keep eight unsupported refusals.
- [x] Add automatic provider-date review against checksummed 2025/2026 HOSE/HNX
  calendars and amendments, requiring the exchange schedules to agree. Reference
  window has 247 dates. Preserve per-provider missing/unexpected date candidates,
  calendar/catalog hashes and partial-collection status; never infer actual
  sessions or market accuracy from date presence. Pass five source/partial-audit
  tests. A partial 28-symbol replay finds VPS has five dates and VNDirect 127
  for every checked symbol; 12 VCI series observe all reference dates. Complete
  collection and full-scope date review remain open.

- [x] Automatically replay captured daily failures through the unchanged parser,
  preserving rejected dates while comparing their valid subsets. All ten failed
  retained daily responses replayed offline: incomplete four-feed observations
  shrink from 5,221 to 33; all malformed dates and original errors remain visible.
  Integrate as automatic daily diagnostics; pass 15 focused tests. No downloads,
  database copies or publication, and unknown timestamp bases stay rejected.

- [x] Automate the full retained VN daily source-value comparison across all 59
  selected symbols for October 4, 2023–October 2, 2026. Preserve 236 feed-window
  results and strict malformed-row refusals. Support correct automatic daily
  versus minute feed selection and read-only SQLite comparison in the same runner.
- [ ] Resolve retained daily provider/basis disagreements. Ten responses contain
  invalid OHLC ranges on 13 distinct stock/date pairs; current stored rows are
  structurally valid, with five matching DNSE exactly and eight differing.
  Keep the captured witnesses and do not replace values by simple provider voting.

- [x] Automatically validate all active cold archive objects against their
  checksums, identities, OHLCV/timestamp rules and indexed row counts/bounds.
  Check 888 objects containing 438,268 rows: no structural failures. Keep the
  one pending-repair object visible as unresolved quality work. Temporary cache
  is removed; no databases or remote objects are created. Integrate into the
  combined automated validation pipeline and pass 18 focused tests.

- [x] Automatically validate every live SQLite OHLCV row in one read-only
  snapshot, with bounded invalid-row samples and quote/futures rules preserved.
  Check 6,022,230 candles across 207 series: no structural violations and SQLite
  integrity passes. Integrate this step into the automated provider pipeline;
  keep the 22 KB report without database copies. Twelve focused tests pass.
  Structural validity is separate from market accuracy and completeness.

- [x] Add one automated OHLCV review command that inventories selected storage,
  compares all four native VN minute providers and checks SQLite against them.
  Run it across all 59 selected VN symbols for October 2, 2026: 236 provider
  windows, no request errors, no conflicts against unanimous provider values
  and no missing unanimously supported timestamps. Report all other differences
  as exceptions; never select a winner or mutate candles. No database copies.
- [ ] Extend automated cross-provider checks to bounded pages spanning the full
  retained daily/minute windows and cold archive boundaries. Keep unsupported
  ranges and provider-basis disagreements explicit, prioritize material OHLCV
  exceptions, and avoid making routine progress depend on manual ticker research.

- [x] Verify NAB transfer boundaries against signed HNX/HOSE notices. Annotate
  its six absent 2024 daily dates as the transfer interval, keep both boundary
  sessions expected and preserve every original stock candidate date list.
  Recheck the current live database read-only, retain five small evidence objects
  in local RustFS with exact readback, and pass 46 calendar review tests.
  This explains dates only; VTP/BSR event boundaries and OHLCV accuracy remain open.

- [x] Remove completed test databases, including unnecessary compressed copies.
  Preserve live data, current rollback image, pending index candidates and source
  captures. Add mandatory `AGENTS.md` lifecycle rules and automatic temporary
  directory cleanup to full-database refresh/rollover/worker checks.
- [ ] Bound historical S3 manifest retention while preserving every active,
  recovery and explicitly retained rollback reference. The local RustFS audit
  found 2.01 GB of manifests out of 2.58 GB total; do not prune by age alone.
  Keep disposable full-database rehearsals temporary instead of accumulating
  permanent copies after every check.

- [x] Verify GEE's last UPCoM and first HOSE trading sessions from signed
  exchange notices. Add opt-in, source-bound transfer annotations to the stock
  date review, retaining original absences and observed conflicting rows. Both
  boundary sessions remain expected. All 18 GEE daily absences are explained by
  the July 19–August 13, 2024 transfer interval; the two separate hourly absences
  remain open. Preserve five source/declaration/review artifacts in local RustFS
  with exact readback and pass 876 API tests. Other stocks' event boundaries and
  individual OHLCV values still require evidence; no runtime exclusions or
  candle publication are licensed by this annotation.
- [x] Verify signed HNX schedules across 2023–2026, including the required 2024/
  2026 amendments. Bind every source byte and record official versus mirror
  distribution. Both exchanges have equal scheduled weekdays across retained
  windows. Add a read-only stock date-candidate review with mandatory source
  checks, preserving the HOSE index scope guard. Audit all 57 stocks daily/minute
  and 53 configured hourly series. Every stock minute series contains all 247
  scheduled retained dates; daily/hourly absences remain explicit candidates.
  Actual merged SQLite/S3 reads confirm the candidate dates are still absent.
  Preserve 22 evidence artifacts with exact local RustFS readback and pass 863
  API tests. Stock listing/transfers, suspensions, no-trade evidence, per-candle
  completeness and OHLCV values remain separate open gates.
- [x] Extend the source-backed index schedule audit across the entire retained
  three-year daily/hourly and one-year minute windows. Visually verify 2023/2024/
  2026 annual notices and required 2024/2026 amendments, preserving mirror versus
  official distribution metadata. Require every amendment source with exact byte
  binding. Both indices have all 746 scheduled retained daily dates and all 247
  scheduled retained minute dates. Check the actual SQLite/S3 history reader:
  native hourly coverage is only 138/746 dates per index, with no primary hourly
  archive to fill the 608 absent dates. Isolated hourly candidates cover all 746
  dates but still omit 45 current timestamps and remain unlicensed for publication.
  Pass 851 API tests and preserve 26 artifacts in local RustFS with exact readback.
  Date presence does not certify prices, volumes or complete intraday timestamps.
- [x] Establish a source-backed announced-calendar audit independent of provider
  observations. Download and visually verify HOSE's signed 2025 notice, bind its
  bytes to the declaration, and check both indices in one read-only SQLite
  snapshot. VNINDEX/VN30 each contain all 249 scheduled 2025 daily dates, with
  no holiday/weekend observations or shifted timestamps. Twelve focused tests
  pass. Preserve the PDF, declaration and populated audit in local RustFS with
  exact readback. Scheduled-date coverage does not certify OHLCV accuracy or
  actual exchange operations; other years/exchanges and stock-specific listing,
  suspension and no-trade evidence remain open.
- [x] Expand the remaining VN minute-basis investigation to complete daily
  windows from all four feeds and 44 exact-date VCI controls. All 36 stock
  controls align with retained daily prices within one VND; eight index controls
  remain discrepant. Preserve GEE/VN30 daily source errors independently.
- [x] Stage eight additional coherent stock minute candidates across their
  entire existing September 2025–October 2026 scope (468,309 total candles).
  Resume failed isolated captures from verified cursors after corroborating
  single-minute defects; preserve source evidence and canonical rows.
  Full-date/native-volume/archive reviews pass BSR, TCB, VHM and VND. These are
  now activated locally after fresh handoffs and guarded publication.
- [x] Generalize verified stock minute activation with full immutable minute/daily
  source replay, exact native volume witnesses on every observed date and fresh
  completed-tail controls. Detect stale passed reviews, altered source/candidate
  bytes and changed daily witnesses. Publish BSR/TCB/VHM/VND locally with complete
  backups, 142 verified source-evidence objects, 72 boundary cases, 112 HTTP
  checks and exact eight-object/four-series restoration. Preserve all 242,556
  existing observed timestamps and all unrelated VN OHLCV/provider basis.
  Curate ten replayable proofs for the six active VCI series; broader proposals
  remain isolated. Current audit leaves CTG/GAS/GEE/MWG/SHB/VN30/VNINDEX open.
- [x] Prove CTG's two separate closed-day volume contradictions with explicit,
  bounded multi-correction evidence. Each proof corrects only its exact target;
  all contradictions must reconcile with cumulative totals and two native peers.
  Stage all 60,723 candles, replay 31 source pages and three native daily captures,
  and verify all 270 observed dates. Activate locally after a fresh 2,000-candle
  handoff, preserving every timestamp and immutable before-images. Verify a real
  40-row refresh, 18 boundary queries, 28 HTTP cases and exact two-object S3
  restoration. Keep 15 curated proofs for seven active VCI series.
- [x] Recover GEE's native volume corroboration while retaining the rejected
  VNDirect OHLC row. Verify 269 valid daily candles in separate bounded requests;
  validate captured volume fields independently with explicit field scope,
  request/source/symbol bindings, hash checks and all OHLC rejections retained.
  GEE's full 48,515-candle candidate passes all 270 observed dates, fresh native
  overlap, guarded local activation, 40-row refresh, 18 boundary queries, 28 HTTP
  cases and exact two-object S3 restoration. Keep 16 curated volume proofs for
  eight licensed VCI series; GAS/MWG/SHB/VN30/VNINDEX remain unresolved.
- [x] Reprobe GAS and both discrepant MWG dates across all four native providers,
  using three VCI page sizes and preserving all raw captures. Replay source
  responses offline and independently verify a single peer-backed GAS volume
  proposal. Both minute peers place the extra 200 shares on an existing minute;
  they do not supply the apparently missing timestamp. Ten focused regression
  tests pass. The proposal cannot license runtime publication.
- [x] Add durable, replayable peer-minute volume proofs distinct from cumulative
  correction proofs, then re-stage/review/activate GAS only if every gate passes.
  GAS's July 10 target is 09:00 UTC: native peers report 1,300 versus VCI's 1,100,
  with 181 identical timestamps and all other volumes matching. Corrected totals
  match both daily peers and final cumulative volume. Never synthesize the absent
  09:01 candle or move volume to 09:02 to force reconciliation.
  The freshly re-staged 55,800-candle GAS history passes all 270 observed dates,
  full immutable source replay and a fresh 2,000-candle native overlap. Activate
  locally with whole SQLite backup and immutable hot/cold before-images; verify
  40-row refresh, 18 boundary queries, 28 HTTP cases and exact S3 recovery.
  Retain all four minute/daily witness captures behind the peer proof in S3,
  plus the original VCI capture. Curate 20 proofs for nine licensed VCI series;
  MWG/SHB/VN30/VNINDEX interval-basis findings remain open.
- [x] Recheck all four daily feeds and five native minute dates for SHB.
  Fresh responses reproduce all 48 observed DNSE binary32-equivalent volume
  differences, with exact VNDirect and captured cumulative corroboration.
  Add explicitly declared rounded witnesses, preserving every raw and canonical
  value and keeping provider storage type unverified. Default gates stay exact;
  edited declarations, source bindings, missing exact peers and changed cumulative
  prefixes reject the scoped policy. Full source replay, fresh 2,000-candle overlap,
  guarded activation, 40-row refresh, 18 boundary queries, 28 HTTP cases and exact
  S3 restoration pass for all 60,985 observed SHB candles. Retain the declaration
  and all input captures in immutable S3 evidence. Curate 22 correction proofs for
  ten licensed VCI series; MWG/VN30/VNINDEX interval-basis findings remain open.
- [ ] Reconcile MWG's two full-day volume differences.
- [x] Locate both MWG residuals using eight new four-feed hourly controls and
  24 native closing-session queries at 1/5/15/30-minute resolutions. Replay minute,
  hourly and both daily native responses before diagnosis. DNSE's first four hours
  match minute sums exactly; the final hour accounts for the entire 2,600/600-share
  residual. Both native daily peers match the native hourly totals. All smaller
  native resolution queries are empty/no-data. Ten actual-fixture regressions
  pass; the diagnosis explicitly cannot attribute a minute or license publication.
  Native trade/minute evidence or independently verified session/venue semantics
  remain necessary; do not force an hourly residual into an auction candle.
  Do not assign missing volume to an adjacent candle or silently normalize peers.
- [x] Implement the explicitly approved opt-in VCI minute-history fallback.
  Keep the three preferred providers and daily/hourly sourcing unchanged; append
  VCI only for older minute requests. Validate units, symbol, arrays, timestamps,
  duplicates and volume; stage unverified fallback work without publication.
  Existing per-series snapshot proof licenses VCI reads/updates. Preserve
  certificates through storage validation and retain the normal correction guards.
- [x] Capture coherent isolated FPT/TPB VCI minute candidates, preserving every
  original hot timestamp. FPT spans August 2025–October 2026 with 65,745 rows;
  TPB covers the retained year with 54,332. Their minute-derived daily prices
  agree with retained daily references within one VND on all 291/247 observed
  dates. Fresh oldest/middle/latest controls match all 12,000 sampled candles.
  Candidate completion flags certify observed reference coverage, not full
  exchange calendars or permission to publish. Canonical OHLCV stays intact.
- [x] Publish and license the verified local FPT/TPB minute candidates across SQLite/S3 with
  immutable before-images, source witnesses and cold/recent boundary checks.
  their corroborated 200/900-share corrections. FPT has 66,198 total rows and
  TPB 59,512; all existing observed timestamps survive. Verify fresh 2,000-row
  native handoffs, successful 40-row refreshes and all 36 minute-derived boundary
  queries. Preserve a whole SQLite backup and immutable S3 before/after images.
  VPS/legacy daily-volume coverage remains a separate unresolved convention;
  minute totals match VNDirect/DNSE peers without scaling prices or volume.
- [x] Add atomic frozen VN minute snapshot activation. Verify immutable hot
  before/after images, original archive objects and a prepared receipt before
  changing SQLite. Replace retained candles, archive pointers and series state
  together; guard stale captures, active workers, history markers and old source
  attempts. Preserve cancelled-job staging. Rehearse actual FPT/TPB originals
  and corrected captures in isolated SQLite/RustFS, then restore exact row
  versions and pass all 36 boundary queries. Source licensing, native handoff
  and canonical execution are now verified for FPT/TPB locally; storage activation
  is not a market proof for other symbols or missing exchange sessions.
- [x] License same-provider refresh of a coherent native VCI snapshot with a
  replayable exact completed overlap. Require at least 1,000 candles across
  five observed sessions through the published completed tail, preserving every
  overlap timestamp and exact OHLCV. Guard concurrent changes/workers, preserve
  explicit volume normalization proofs and retain legacy snapshot freeze rules.
  Verify real FPT/TPB 2,000-row handoffs, 40-row worker refreshes and restored
  native certificates in isolated RustFS, followed by verified local activation.
- [x] Reject VCI consecutive-minute volume contradictions when cumulative
  totals are supplied. Captured FPT/TPB regressions reproduce both defects;
  sparse minutes and VN daily resets never trigger guessed allocation.
  Corrections remain isolated proposals, requiring peer evidence and coherent
  publication rather than silently replacing raw source volumes.
- [x] Add explicit replayable VCI volume proofs for the corroborated FPT/TPB
  defects. Require one consecutive-minute contradiction, a complete observed
  cumulative prefix, reconciled session total and matching VNDirect/DNSE daily
  volumes. Apply only to an exact unchanged source candle/cumulative pair;
  preserve OHLC, timestamps and provider/revision. Persist applied proofs in
  SQLite, protect unlicensed worker publication and preserve raw/proof evidence
  in local RustFS with exact readback. Keep runtime enablement explicit.
- [x] Compare TPB's complete three-year daily window across all four feeds.
  VNDirect/DNSE daily volumes match retained minute sums on 246/247 dates;
  the remaining date has one VCI cumulative-volume contradiction. VPS/legacy
  report small additional daily volumes on 246 dates. Their chart coverage
  convention is unproven; do not label this dividend scaling or assume odd lots.
- [x] Audit all seven active primary FPT/TPB minute archives against pinned VCI
  pagination. All three TPB partitions and two FPT October partitions match
  exactly. FPT September is quarantined by the cumulative-volume guard;
  FPT January has a different primary price basis, while the newer frozen
  public snapshot matches VCI and minute-derived daily prices/volumes agree
  with the retained daily reference. Preserve these originals during migration.
- [x] Rehearse coherent corrected FPT/TPB minute snapshots across SQLite and
  actual local RustFS using isolated storage. Preserve all observed canonical
  timestamps; verify full OHLCV/provider/revision readback and 36 query cases
  across the retention boundary against all-SQLite references, including SMA
  and EMA. Keep source correction licensing, evidence publication, race-safe
  canonical replacement and live refresh adoption were subsequently verified
  for these two local series; broader coverage and production cutover remain open.
- [x] Diagnose same-feed minute/hour differences for all selected VN tickers.
  VNDirect excludes late-session observations in 12 captured hours; legacy does
  so in three. Other observed aggregation disagreements remain explicit. Captured
  HCM/HAG regressions reproduce the exclusions without inventing trades.

- [x] Compare VPS, VNDirect, DNSE and legacy as peers for all 59 selected VN
  tickers: September 7–October 2 daily and October 1–2 hourly/minute. Capture
  708 replies with no request/parsing errors; compare all six feed pairs and
  preserve timestamp, price and volume disagreements separately. Legacy is the
  sole three-against-one outlier on nine VIB daily volumes, all already matching
  the native consensus in SQLite. No feed is declared authoritative. Six audit
  regressions and the 594-test suite pass. See the four-feed validation section.
- [ ] Investigate the four-feed material-price review cases (VNINDEX daily,
  TPB hourly/minute, IDC hourly/minute, HCM hourly and HAG hourly), plus timestamp
  coverage differences. Establish adjustment, aggregation and session semantics
  with independent evidence before licensing corrections; a majority vote or
  legacy disagreement alone does not determine correctness. This recent audit
  does not certify the entire three-year daily/one-year minute history.

- [x] Prioritize valid requested OHLCV over optional indicator warmup, following
  the revised acceptance policy. Use the longest verified recent context without
  crossing gaps or revisions; require complete SMA periods and accept finite
  history EMA estimates. Grow aggregate reads from observed bucket density.
  Six regressions and all 588 tests pass. All 2,103 populated query checks now
  pass, preserving every OHLCV projection against 701 previous raw controls;
  ten optional-context failures resolve. Actual requested-history defects,
  source ambiguity and production migration gates remain open. SJC OHLCV is
  explicitly documented as buy/sell quote-derived with placeholder volume.

- [x] Keep live ingestion advancing during slow archival uploads with one
  background maintenance task per worker. Prevent overlapping maintenance
  batches and drain active work on bounded exit/cooperative shutdown before
  releasing job claims. A blocked-transfer regression reproduces the old stall,
  then verifies three cycles/new candles, preserved unverified local data and
  exact final archive readback/pruning. Existing repeated-SIGTERM subprocess
  checks pass. This does not certify multi-day operation or provider completeness.

- [x] Match legacy integer query widths/syntax for candles and analysis routes,
  reject invalid bounds before market reads, and preserve zero-valued clamps,
  empty sector lists and signed RRG volume controls. Restore volume-profile's
  case-insensitive source dispatch/default VN behavior and empty-symbol error
  precedence. Twenty endpoint regressions pass; a compiled Rust parser check
  corroborates integer syntax. The running API preserves six completed-date
  controls byte-for-byte and passes seven corrected-input checks while workers
  continue. Wider data/production acceptance remains open.

- [x] Preserve the legacy sync UUID input contract. Reproduce eight malformed
  inputs accepted by Python but rejected by Rust's pinned parser, then return
  the existing HTTP 400 envelope for both reads and writes. Four accepted UUID
  forms retain one canonical record, creation time and secret protection.
  Twelve new endpoint regressions pass. Private production sync inventory and
  data export remain separate, unfulfilled migration gates.
- [x] Recheck the remaining five stock-hourly session gaps with narrow and
  wider requests to all three native sources and exact public exports. Preserve
  their continued absence, public candidate bars and GEE's cross-provider volume
  difference without fabricating sessions. VNDirect's market-price endpoint
  repeats the invalid VNINDEX 2020 range. Store 48 immutable evidence objects
  with exact RustFS readback; this closes the probe, not the unresolved data gates.

- [x] Verify cooperative termination and real local restart of all three scoped
  native workers with the latest shutdown correction. Confirm old processes are
  terminal before replacements start; all 57 stock, four crypto and four enabled
  global minute series record fresh successes on their existing provider/revision.
  Consistent images preserve formerly verified completed values, every frozen
  and index record version, series states, archives, certificates and imports.
  Provide a reusable read-only verifier and reject stale/wrong-frame successes
  and changed protected records. API PID 16546 remains running. This verifies
  one restart; real supervisor recovery and multi-day operation remain open.
- [x] Roll out nonblocking archive maintenance (`26bd316`) to all three local
  workers and verify another populated restart. All 65 targeted minute series
  refresh; completed values, frozen/index versions and archive/series metadata
  remain exact. API PID 35486 continues running. Same-day maintenance has no
  objects due; slow-transfer concurrency is covered by the SQLite/Parquet
  regression, with multi-day supervised operation still open.
- [x] Make SQLite restore independent of the configured runtime database.
  Restore works when that file is missing, corrupt or has a newer schema;
  preserve candles and sync records, open backup filenames safely, reject bad
  backups without creating a destination, and refuse existing/runtime targets.
  Seven operational regressions pass; a populated 6,020,562-candle restore is
  byte-identical, with all 13 tables independently compared. Production sync
  export remains open.
- [x] Preserve dated partial aggregate buckets. Clip native observations before
  aggregation and retain the first bucket's timestamp even before `start_date`.
  Thirteen regressions cover weekly/fortnightly/monthly input bounds, limits,
  indicators and SQLite/Parquet reads. The populated 71-ticker audit passes
  2,093/2,103 requests, restores 414 previously empty requests and keeps all
  1,679 previous successes byte-exact. Ten VND/VNINDEX context guards remain.
- [x] Check whether valid older-year alternatives can repair the remaining daily
  context guards without a retained provider transition. Six fresh 60-row
  overlaps preserve actual price/volume disagreements; both complete VND
  alternate windows cover 746 dates but change prices on 583/658 dates. Preserve
  twenty evidence objects with exact RustFS readback. A coherent full-history
  transition remains open; recent overlap cannot license mixing providers.
- [x] Attempt complete coherent VND daily transitions on both historical
  alternatives, requiring all 1,927 original observed dates. VNDirect repeats
  an invalid 2019 range; DNSE repeats conflicting 2022 candles. Preserve ten
  checksummed evidence objects and initial/retry witnesses; publish neither
  failed candidate. Resolve source ambiguity before licensing a transition.
- [x] Prepare Linux host supervisor examples for the API and the three current
  native worker scopes. Require per-source argument files, keep both indices
  excluded from VN, and preserve RustFS-only Compose. Parse the argument files
  with the real CLI and inspect the configured/native states: 167 VN, 12 crypto,
  13 Yahoo. The staging runbook requires actual manager recovery and UTC-day
  archival checks. Linux unit verification/activation remains unperformed on
  this macOS workspace; examples do not certify supervised uptime.

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
- Treat VPS, VNDirect, DNSE and the legacy API as four comparison feeds, with
  no authoritative legacy baseline. Interface compatibility does not require
  preserving incorrect legacy prices. Audit same-date/interval prices, volumes
  and timestamp coverage across all six feed pairs. Agreement is evidence,
  not independent truth when feeds share an upstream. Verified coherent
  corrections may supersede legacy values while preserving originals for rollback.
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
- Prefer VPS, VNDirect and DNSE. The newer explicit user approval permits VCI
  historical-minute probes and a verified fallback. VCI is opt-in, minute-only
  and last in older-window fallback order; unverified candidates stay staged.
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
  fallback rules based on evidence. The original VCI exclusion is superseded
  by the explicit minute-history fallback approval; preferred providers stay unchanged.
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
  `../../../legacy/rust/aipriceaction/src/workers/vci_shared.rs`, `vci_dividend.rs`, and
  `../../../legacy/rust/aipriceaction/src/queries/vci_recovery.rs`: ignore unfinished candles,
  compare matching completed dates, require corroborating historical changes,
  keep replacement history on a consistent provider, validate newest coverage,
  publish atomically, invalidate caches, and resume repair after interruption.
  These remain design references. VCI use is now authorized only for the
  explicitly approved minute-history probes and verified fallback.
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
- [x] Match Rust's fixed hourly base interval for 1h/4h API reads. Minute-only
  entries no longer synthesize hourly candles through this compatibility route;
  retain native-hourly limitations and honest metadata/health state.
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

## Transport compatibility follow-up — 2026-10-20

- [x] Match Tower's 5 MiB request-limit response (`413`, plain-text
  `length limit exceeded`) and apply Rust's security headers to that early
  response path.
- [x] Match Axum's active-router 404 body, 405 empty body and merged `Allow`
  header, plus static cache lifetimes (one hour fallback, one day for image and
  icon extensions).
- [x] Reject an invalid `/tickers/refresh` mode during body deserialization
  with status 422 and a plain-text response, before route authorization.
- [x] Match Axum's JSON rejection body details for the differential corpus on
  `/tickers/refresh` and `POST /sync/{key}`: syntax versus data status, field and
  nested array paths, parser reasons, byte-based line/column positions,
  duplicates, missing fields, enum values, malformed numbers, unpaired high
  surrogates, recursion limits, and tuple-style sequence input. Broader malformed
  Unicode/number/escape combinations remain outside the tested corpus.
- [x] Match captured static conditional and range behavior, including ignored
  `If-Range`, single-byte ranges, rejected malformed/multipart/unsatisfiable
  ranges, and Rust's static `HEAD` response headers.
- [x] Compare dynamic gzip, Brotli, deflate, and weighted encoding negotiation
  against Rust. The HTTPX client verifies decoded entity content and negotiated
  headers; exact compressed wire bytes are not compared.
- [x] Use one Rust-compatible 180-second request deadline across body reading
  and route work. `API_QUERY_TIMEOUT` no longer overrides the production
  default because the Rust service fixes its timeout at 180 seconds. Both
  implementations return the Rust-compatible empty 408 response on expiry.

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

## Rust/Python/web endpoint compatibility — 2026-10-21

This inventory covers the legacy Rust router, the replacement FastAPI router,
and the interfaces used by the current web client. The caller column separates
page-level calls from SDK/CLI and operational routes. “Implemented” means the
route and feature exist locally; it does not certify byte-for-byte
cross-backend output.
An opt-in shared Rust/Python HTTP differential harness now exists at
`tests/test_rust_python_differential.py`. The previous durable-history run after
the volume-profile history-gap parity fix and the scalar extractor,
populated-Notes-symlink, mode-alias, case-insensitive ticker-info, and
UUID-canonicalization probes passed against Rust and Python on 2026-10-21: 14
passed in 7.29 seconds, with one Starlette/httpx deprecation warning. The first
expanded run found four duplicate Notes query-extractor mismatches; Python now
returns the same 400 responses as Rust. This run includes
invalid top-performer and volume-profile dates,
`±Infinity` score/profile query values, above-clamp RRG, a registered Yahoo
ticker with no hourly bars, Chrono-style unpadded dates, reversed Yahoo/SJC
identity ordering for native, 2W and 1M queries, the Explorer's `with_hour=true`
request, Notes lifecycle/quota/symlink probes, and a volume-profile request
whose available bars intersect a recorded history gap. Rust and Python now
return matching partial profiles for that fixture. The test uses a temporary PostgreSQL
container bound to loopback with tmpfs-only storage; no production database or
persistent test volume is used. The comparator checks status and application
headers, exact CSV and non-JSON bodies, JSON scalar types and recursive object
key order; HTTPX decompresses encoded responses before body comparison and does
not compare float spelling or compressed wire bytes.
The durable-history paired matrix passed on the rebuilt Rust binary (one matrix
test in 8.64 seconds). Earlier seeded live-Redis runs covered negative and zero
raw limits, malformed and duplicate members, end-bounded timestamps, per-ticker
and joined-snapshot hits, null performer/sector metrics, and duplicate VCB
membership in RRG. The complete Python suite now passes 1,502 tests with one
skip in 399.51 seconds; the Rust suite passes 20 tests, and Python analysis
compatibility passes 52. The latest rebuilt-binary/live-Redis run executed the
single paired HTTP matrix test in 12.50 seconds, including adversarial HTTP
assertions; the test module's total pytest function count is separate from the
number of HTTP requests in that matrix.
Ruff, scoped formatting, Python compilation and `git diff --check` pass. These
checks do not establish 100% compatibility: production data revision parity,
extended-year joined timestamps, remaining analysis/data boundaries, and
browser/SSR integration remain open. The web repository was inspected read-only
and was not changed.

The expanded paired matrix was rerun with live Redis after extending the fixture
to cover undated latest RRG for `vn`, `crypto`, `yahoo` and `all`; latest MA
scores across all periods and EMA settings; every top-performer sort/direction;
successful crypto and Yahoo volume profiles; and additional refresh, sync and
Notes authorization failures. The run first exposed the legacy Yahoo MA-sector
lookup and volume-profile reduction-order differences; Python was aligned to
Rust, and the final paired run passed (one matrix test in 7.49 seconds). This is
same-fixture endpoint parity evidence, not production-data or browser evidence.

| Endpoint | Caller / exposure | Contract and web-facing features | Current status |
|---|---|---|---|
| `GET /tickers` | Home, chart, alerts, mixed watchlists and AI pages; CLI/SDK. | `symbol` (repeated or comma-joined), `interval`, `start_date`, `end_date`, `limit`, `legacy`, `format=json\|csv`, `cache`, `mode`, `redis`, `ma`, `ema`, `snap`; returns OHLCV, indicators and change fields. Home-page VN/crypto/Yahoo and chart intervals; mixed-source alerts/watchlists/AI use `mode=all`; Rust mode aliases `stock`/`stocks` and `cryptos` are covered. | Implemented in both. The durable shared-fixture run passes homepage CSV/JSON, dated/native and derived intervals, comma/repeated symbols (including a literal comma-bearing symbol versus two separate symbols in both cache-request orders), empty symbol, error and response-cache cases. The latest matrix independently checks homepage CSV headers, selected symbol rows, dates, closes, no-MA blanks, populated EMA columns and an EMA10 formula. Caller-shaped homepage `end_date` CSV and symbol-specific daily/hourly/minute CSV chart cases pass in the latest paired matrix run. Adversarial additions cover invalid typed mode, `HEAD /tickers`, CSV prices at formatting thresholds plus zero and negative values, and the empty SQL response for `limit=-1&ma=false`. The no-Redis latest/end-only weekly test independently checks exact aggregate OHLCV, changes, SMA values and row count on an 8,701-bar FPT history; the live-Redis case derives the same oracle from the exact retained 5,000-bar tail. The live-Redis paired matrix compares signed/end-bounded raw reads; seeded per-ticker snapshot hits; stale, incomplete and malformed snapshot behavior; absent, malformed, empty and subset ticker-list membership in single-source and `mode=all`; explicit source-priority fallback; partial raw batches; and empty-batch SQL fallback. A Redis-only FPT tail sentinel proves snapshot misses read Redis rather than SQL. It asserts `x-data-source` and `x-redis-*` values for these branches. Python supports `REDIS_URL`, reads/writes Rust-compatible per-ticker snapshots only for eligible native, MA-enabled, undated/current-end, single-source requests, and honors `snap=false`; its `cache` flag remains a separate in-memory response cache. The worker integration test runs a successful `Worker.sync` with a shared Redis test double, verifies bar publication and stale-snapshot invalidation, then verifies the FastAPI route returns the worker's latest close and refreshes the snapshot. Worker restart/rehydration after a durable database import and production cache configuration remain open. Python can load Rust's ordered `id,source,symbol` export through `RUST_TICKER_IDENTITY_ORDER_PATH`; exporting and applying the actual production mapping remains open. Undated reads skip pending archive-repair objects older than the latest trusted published/live candle; pending-only or same/newer repair data is omitted when no live candles exist, and pending data at or beyond the latest live row remains a 503 rather than returning stale data. Dated and end-only requests remain strict. The latest VN CSV returned 1,559 rows including VTH; the earlier local Rust smoke returned 59 symbols without VTH, so these live databases/catalogs are not a matched input comparison. The web typed SDK omits `redis` and `snap` from `TickersQueryParams`. |
| `GET /health` | Web pages; operations. | Counts, last-sync values, uptime, trading-hours fields and runtime/cache metrics. | Implemented in both; complete stable health schema and values match on the shared fixture, including when PostgreSQL `pg_class.reltuples` is explicitly stale. Exact Rust record totals use per-ticker/per-interval counters maintained transactionally by an `INSERT`/`UPDATE`/`DELETE` trigger; the initial baseline is reconciled one ticker per transaction under a per-ticker advisory lock. Existing identities suppress deltas until their baseline is complete, so deletes during reconciliation cannot make partial counts negative. The fixture reruns reconciliation over populated data, tests writes before a ticker is reconciled, and checks delete/insert/update behavior for daily, hourly and minute rows plus a ticker-and-interval move. Both services publish all-or-nothing health snapshots every 30 seconds, retain the last good snapshot on refresh errors, and retry an initial failure immediately. Their exact values can still diverge when their underlying data revisions differ. Worker, memory and disk-cache fields remain zero placeholders. The per-candle trigger adds advisory-lock and counter-row work to writes; benchmark representative bulk imports and concurrent interval workers before production cutover. |
| `GET /tickers/group` | Web pages. | `mode`; sector/group membership for VN, crypto and Yahoo. | Implemented in both; VN/crypto/Yahoo/all catalog responses pass the isolated shared-fixture matrix. Other catalog revisions remain a data-parity concern. |
| `GET /tickers/name` | Web pages. | `mode`; symbol-to-name map. | Implemented in both; default, crypto, Yahoo and all responses pass the isolated shared-fixture matrix. Other catalog revisions remain a data-parity concern. |
| `GET /tickers/info` | Operations; no sibling web page/client call found. | Optional, case-insensitive `ticker`; full instrument metadata or one instrument. | Implemented in both; one ticker, lowercase ticker lookup, not-found and full no-query responses pass the isolated shared-fixture matrix. This route is not exposed by the sibling `AIPriceActionClient`. Other catalog revisions remain a data-parity concern. |
| `POST /tickers/refresh` | Packaged Explorer; operations. | JSON `interval`, `mode`, `key`; refresh authorization and schedule/error behavior. | Implemented in both and called by the packaged Explorer. It is not exposed by the sibling `AIPriceActionClient`. Python matches captured Axum media-type, syntax/data, field path, enum, missing/duplicate-field, line/column and size-limit responses. The latest paired matrix independently checks the exact success body and schedule effects for four modes (`vn`, `crypto`, `yahoo`, `all`) across `1D`, `1h`, and `1m`: selected schedule rows become fresh, Rust/Python timestamps agree within one second, and unrelated rows remain unchanged. Successful response key order matches. Paired MIME cases cover `application/foo++json`, a terminal empty parameter and a rejected empty quoted parameter; direct byte-level parser tests cover quoted HTAB and DEL rejection. Additional malformed JSON/header combinations remain open. |
| `GET /sync/{key}` | Web/SDK authenticated state. | Bearer auth plus `secret` query; UUID path parsing/canonicalization; returns saved private sync record. | Implemented in both. Paired HTTP probes verify canonical retrieval after writing with a compact UUID, auth and query failures, unset/empty `SYNC_TOKEN`, Rust-compatible whitespace trimming, and rejection of non-ASCII authorization header bytes. Private production sync records are not part of public endpoint migration and still require separate inventory/export and authenticated acceptance. |
| `POST /sync/{key}` | Web/SDK authenticated state. | Bearer auth plus JSON `secret` and `value`; UUID path parsing/canonicalization; creates or replaces sync record. | Implemented in both. Authenticated requests and the captured syntax/data rejection corpus pass HTTP comparison, including nested serde paths, numbers, Unicode, recursion limits, sequences, trailing input, compact-to-hyphenated UUID canonicalization and chunked size errors. The paired write/read fixture now uses the web `SyncData` shape (`watchlists`, `alerts`, `chartLines`, `exportedAt`); the caller-shaped web payload passed the latest paired rerun. Private production-record migration and additional malformed JSON/header-byte combinations remain open. |
| `GET /analysis/top-performers` | Packaged explorer; SDK/helper/examples. | `mode`, `date`, `ema`, `sort_by`, `direction`, `limit`, `min_volume`, `sector`, `snap`, and Explorer's `with_hour=true`; returns top and worst performers with MA and sector fields. | Implemented in both for the shared Rust contract; seeded responses, invalid-date echo, sort fallback, zero limit and `min_volume` overflow cases pass HTTP comparison. Python filters exact sector membership and uses deterministic metric/symbol/source ordering. The paired matrix forces equal metric ties in single-source and `mode=all` cases and checks symbol/source order; invalid typed mode is covered too. A request-scoped overlapping-group fixture requires both servers to label FPT with the lexicographically first sector (`Alpha`). A per-symbol `DataError` (such as a local unresolved gap) is omitted while other tickers remain available, matching Rust's batch fallback behavior. The route forwards `snap`; Python reads and writes Rust-compatible joined snapshots when a complete source set is available, and `snap=false` skips reads while still refreshing snapshots after raw Redis/SQL fetch. The live-Redis matrix forces a complete joined-snapshot hit with VCB null optional metrics and asserts the sentinel row. New paired cases also cover stale single-source snapshots and complete/incomplete joined-snapshot sets in mode=all; an incomplete Yahoo set falls back for that source to raw Redis/SQL. The packaged Explorer's “Daily + Hourly” option sends `with_hour=true`, but Rust ignores it and returns `hourly:null`; Python matches that behavior, so the advertised hourly breakdown is not provided. The typed SDK omits `mode`, `snap` and `with_hour` from this query's parameter interface. |
| `GET /analysis/ma-scores-by-sector` | Packaged explorer; SDK/helper/examples. | `mode`, `date`, `ema`, `ma_period`, `min_score`, `above_threshold_only`, `top_per_sector`, `snap`; returns sector scores and ranked constituents. | Implemented in both; seeded responses plus invalid-period, threshold, NaN and `±Infinity` cases pass the shared matrix (the infinite thresholds serialize as `null`). Python follows the curated analysis universe and excludes the Python-only `ALL_STOCKS` ingestion group. It also preserves Rust's single-source behavior: sector definitions remain VN groups, while rows come from the requested source; this can leave crypto empty and can place Yahoo rows into matching VN sectors. The latest paired matrix covers VN/crypto/Yahoo/all, all MA periods, and SMA/EMA. A per-symbol `DataError` is omitted while available tickers continue to contribute. The route forwards `snap`; Python uses complete-set joined snapshots with raw Redis/SQL fallback, and `snap=false` still refreshes snapshots. A controlled live-Redis joined-snapshot hit seeds VCB with a valid MA20 and null MA20 score; Rust and Python both omit it. New paired cases cover complete and incomplete cross-source snapshots in `mode=all`, including source-local fallback after removing Yahoo AAPL from Redis. Local tests also cover missing MA/score values. Python skips same-source duplicate MASCORE group membership like Rust; paired null/duplicate fixtures pass. Equal-score/average ties exercise deterministic ticker order and lexicographic sector-label selection, including the adversarial `mode=all` fixture; invalid typed mode is covered. The typed SDK omits `mode` and `snap` from this query's parameter interface. |
| `GET /analysis/volume-profile` | Web analysis page; SDK/CLI. | `symbol`, `mode`, `date` or `start_date`/`end_date`, `bins`, `value_area_pct`; returns POC, value area, per-price levels and statistics. | Implemented in both. Python models Rust's retained 20,000-minute tail-selection rule, integer-tick accumulation, newest-first ordering, and sequential floating-point reductions; it sums the unbinned profile before binning, accumulates per-level percentages in Rust's order, and matches the median boundary and value-area threshold operation. It emits Rust-compatible floating-point price fields, rejects per-bar expansions over one million price steps, and matches the empty-profile body. Bounds, NaN/`±Infinity`, malformed dates, partial data overlapping a known gap, and successful crypto/Yahoo profiles pass the shared matrix. Added paired cases check conflicting `date` versus range bounds, start-only and end-only bounds, and invalid typed modes. Exact widget requests using `bins=10` with no `value_area_pct`, a zero-volume successful response, and the following daily CSV fallback request pass in the latest paired matrix run. Only case-insensitive `crypto` and `yahoo` choose those sources; other mode strings fall back to VN. The route reads the actual Redis minute tail when `REDIS_URL` is configured and uses the durable history fallback when it is not; durable reads ignore Python's separate history-gap ledger, matching Rust's available-row behavior. New paired cases verify that a partial overlapping Redis tail is used without SQL completion and a tail with no date overlap falls back to SQL. The BTCUSDT no-minute-history request independently checks the exact 404 JSON body. Production freshness remains open. |
| `GET /analysis/rrg` | Web page and mixed custom watchlists (`mode=all`). | `mode`, `date`, `ema`, `algorithm=jdk\|mascore`, `benchmark`, `period`, `trails`, `min_volume`, `snap`; returns RRG points/trails and source/sector. | Implemented in both; dated JDK RRG and selected JDK/MASCORE mode/trail cases pass on the seeded fixture. Python aligns curated membership, source ordering, the exact 101-row latest MASCORE seed window, and `mascore&trails=0` latest-row/null-score behavior. Lower/upper boundaries, above-clamp (`period=51&trails=121`), unpadded Chrono date, invalid date and volume threshold probes pass the latest shared matrix. `mode=all` independently verifies Yahoo and crypto benchmark fallback and VN-before-Yahoo priority when both sources contain the benchmark ticker `DUPL` with distinct data. Python JDK and historical MASCORE paths now prefer bounded Redis ZSET tails and fill misses from durable history; latest MASCORE uses joined snapshots when the complete source set is present, then raw Redis/SQL fallback. `snap=false` disables snapshot reads but not raw Redis reads, matching Rust; JDK's `snap` is inert in Rust too. Fake Redis tests verify JDK Redis-over-SQL behavior and latest MASCORE snapshot refresh/use. The shared fixture lists VCB twice in one VN group; paired JDK and MASCORE responses match Rust, and Python MASCORE emits the source/ticker once like Rust's map. Python now rejects joined snapshots with null required OHLC fields, matching Rust's typed row decode; local unit tests pass. The live-Redis paired valid-stale and null-required-OHLC fallback cases pass. The latest matrix also checks complete `mode=all` joined snapshots and per-source fallback for an incomplete Yahoo set. Its overlapping-group HTTP case requires both servers to label FPT with the lexicographically first sector (`Alpha`). Static review confirms both Redis readers cap raw members before timestamp deduplication and choose the highest crawl timestamp; the paired live-Redis duplicate-member seed-depth case passes and verifies both servers use the same 99 unique rows after the raw 201-member cap. Production latency remains unverified. The earlier 57.56s Python versus 2.58s Rust timing was a durable-history-only run before Redis-first RRG; it is not a current matched-Redis comparison. A new bounded loopback benchmark validates the JDK response shape, repeated payload hashes, optional cross-server hash and 25-second p95 target; matched Rust/Python timing remains open. The typed SDK omits `snap` from this query's parameter interface. |
| `GET /raw/{path}` | Deprecated SDK helper and packaged Explorer documentation; no active UI call site. | Raw-data passthrough exposed by `AIPriceActionClient.getRawData()`; Explorer lists `/raw/*` as deprecated. | Neither Rust nor Python mounts this retired route. The paired matrix now verifies `/raw/ticker_group.json` returns the same 404 status, headers and body in both. The SDK helper remains unusable if called; this is an identical legacy limitation, not an active web-page request. |
| Legacy image and Markdown-file-delete routes | Dormant Rust helpers and stale upload test script; no mounted route or current web caller found. | `/upload/image`, `/uploads/{session_id}/images/*`, and `DELETE /uploads/{session_id}/markdown/{filename}?secret=...`. | None of these handlers is mounted in Rust or implemented in Python; do not count them as active API routes. The old `scripts/test-upload.mjs` also incorrectly labels the now-mounted Markdown routes as disabled. |
| `GET /explorer`, `GET /public/*` | Python explorer UI/static assets. | Explorer HTML and packaged static assets. | Implemented in both. Python's packaged files match Rust byte-for-byte. The shared fixture compares conditional validators, `If-Unmodified-Since`, `If-Range`, single/multipart/malformed byte ranges, static `OPTIONS`, oversized request bodies, `HEAD` and compression. Local cases cover Tower's empty-file `bytes=0-` and `bytes=0-0` responses. The latest matrix compares GET/HEAD/POST/PUT with populated assets and a missing public directory, including empty 404 bodies and Rust's `Allow: GET,HEAD` header on 405 responses. The matrix also covers `/public` and `/public/` roots, body-limit exemption and HEAD compression. Deployment-derived `last-modified` remains excluded from cross-deployment equality; browser-level replacement smoke remains open. |
| `GET /docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect` | FastAPI-only convenience. | Generated API documentation and schema. | Disabled in Python to match Rust's public route surface. Local tests check GET, HEAD and POST 404 behavior; the latest paired differential run passes the GET, HEAD and POST probes for those paths. OpenAPI generation remains available internally through `app.openapi()` for contract tests. |
| `POST /upload/markdown` | Web Notes feature. | Upload with `session_id`, `secret` and multipart `file`. | Mounted in Rust and Python; the latest shared differential covers upload, quotas, and session/metadata/content symlinks, including a populated symlinked markdown directory whose external content must remain unchanged. Both return 500 for that path. Rust quota locking is process-local while Python uses a filesystem lock; under the current single-API-process Compose topology both serialize quota changes, but multiple Rust API processes sharing one uploads volume could race quotas. Parent-path check/use races remain a filesystem-hardening limitation in both. |
| `GET /uploads/{session_id}/markdown/{filename}` | Web Notes feature. | Public Notes markdown retrieval; Rust accepts an optional `secret` query parameter, which has no effect for Markdown. | Mounted in Rust and Python; paired HTTP tests fetch the uploaded file and reject tested symlink paths. The web app is a client-rendered SPA, so its browser uses the Vite proxy or production API URL. The helper's server-side `127.0.0.1:3000` branch is dormant; browser-rendered Notes acceptance remains open. |
| `DELETE /uploads/{session_id}` | Web Notes feature. | Notes-session deletion using `secret`; browsers issue a CORS preflight for DELETE. | Mounted in Rust and Python; the shared isolated HTTP differential deletes the same disposable session and covers the quota lifecycle and tested symlink paths. The paired matrix also checks the actual `OPTIONS` preflight shape for DELETE. Multi-process Rust quota locking and parent-path check/use races remain as described for upload. |

### Adversarial compatibility follow-up — 2026-10-21

The targeted checks below supersede narrower status notes above. The latest
rebuilt Rust/Python matrix passed all 14 test functions in 15.57 seconds with
temporary PostgreSQL and loopback Redis. It independently asserts that the
comma-joined backtest request returns exactly FPT and VCB, while an exact
registered `DUPL,FPT` symbol remains a single Yahoo result. The combined Notes,
static-method, symbol and preflight regression run passed 14 tests in 19.27
seconds. Ruff checks and formatting passed. Temporary database/Redis resources
were cleaned up; API data stayed 6.0G and RustFS stayed 5.1G. No web-checkout
files were changed.

| Area | Latest evidence | Status |
|---|---|---|
| Route methods and `HEAD` | Added paired invalid-method probes across ticker, analysis, sync, Notes, Explorer and static routes. They require empty 405 responses and each exact `Allow` value. Added `HEAD` probes for every active GET route; each backend must return a populated 200 GET oracle and a bodyless HEAD with matching status, headers and `Content-Length`. The matrix found and fixed the Python Notes `Allow: GET` versus Rust `GET,HEAD` mismatch and nondeterministic `/public` method ordering; focused regressions and the paired gate pass. | Paired server method/HEAD checks pass. |
| Production-origin CORS | The shared matrix sends real web-shaped sync GET/POST and Notes upload/read/delete requests with `Origin: https://aipriceaction.com`, plus sync GET (`authorization`), sync POST (`authorization,content-type`) and Notes DELETE preflights using the same paths and query strings as the calls. Notes multipart POST is a simple request and does not require a browser preflight; its OPTIONS probe is extra coverage. Actual-response cases require the allowed origin and exact exposed-header list. Preflight cases require an empty 200, allowed origin, requested method and requested-header coverage; separate contract tests pin the configured full allow lists, `Vary` and route-specific `Allow`. | Rust/Python local middleware parity passes. The deployed Cloudflare/browser edge remains unverified. |
| Sync missing/secret errors | Added an authenticated GET with the correct Notes secret for a valid but absent sync UUID (404 `Key not found`) and a wrong-secret POST against an existing record (403 `Invalid secret`); the subsequent GET verifies the rejected write did not replace the web-shaped record. | Paired response and no-overwrite checks pass. Production token/configuration and private-record migration remain open. |
| Actual TypeScript client | A temporary, read-only `tsx` probe passed URL serialization, `text/csv` and response capture for 24 Rust/Python homepage CSV responses (12 request shapes per backend). The checked-out `AIPriceActionClient.getTickers()` parser failed eight row-set checks: it drops the synthetic literal-comma symbol `DUPL,FPT` in all four Yahoo homepage CSV variants from both servers. The symbol is absent from the checked-in and local Yahoo catalogs, but the APIs preserve registered symbols containing commas. | Conditional client/API edge mismatch for a supported symbol shape, not evidence of a current production-catalog failure. The sibling web checkout remains read-only, so no parser fix was applied; arbitrary comma-containing catalog symbols prevent a 100% compatibility claim until the parser handles them or that symbol shape is formally excluded. |
| End-to-end browser | The TypeScript probe executes the real query builder and parser with captured backend bodies, but it does not render the current web app or traverse the deployed edge. The app is an SPA, so the active Notes flow is client-side; direct Vite startup may still use its port-3000 proxy default unless overridden. | Open. Keep the web checkout read-only; verify rendered page requests, Notes upload/view/delete, and browser-visible CORS against the local wrapper and deployed edge. |

### Current live deployment comparison — 2026-10-21

These are sampled runtime checks against the currently deployed API, not a
revision-pinned Rust oracle. They separate route/shape compatibility from
production catalog, history and deployment state. The production service was
not changed.

- [x] An adversarial smoke sent the 15 distinct homepage request shapes through
  the LAN Vite proxy at `:5174`; all returned 200. The same request paths also
  returned 200 in production. Six VN/crypto/Yahoo `/tickers/group` and
  `/tickers/name` response bodies matched byte-for-byte.
- [ ] Production's all-symbol VN CSV still returned HTTP 200 with only the
  172-byte header. This remained true for a September end date with response
  cache, Redis and snapshots disabled; an explicit VCB request returned data.
  The same local Python request returned 238,797 bytes in 8.27 seconds with
  rows. Do not treat production's empty body as the desired Python behavior.
- [ ] For a dated September VCB request, production and local Python OHLCV
  matched in the earlier comparison, while EMA50/100/200 differed. The dated
  JDK RRG sample remains 365 production tickers versus 364 local tickers.
  Production and local health also diverge: latest sync dates were October 4
  and October 8, and daily/hourly/minute counts were
  `6,241,960 / 18,679,084 / 852,031,296` versus
  `933,628 / 463,414 / 581,943`. These are data/revision/runtime parity gates,
  not same-fixture route-contract failures.
- [ ] Production reports `x-data-source: postgres`, while the checked-out
  compatibility implementation uses its `database` label. Confirm the deployed
  Rust build and configuration before using production for final parity claims.
- [x] The intended web listener on `0.0.0.0:5173` serves the LAN root and
  `/aipriceaction-api/health` with HTTP 200. Proxy health fields match direct
  Python API health. The user-shaped crypto no-MA CSV returns 200 through the
  LAN proxy and direct API with identical 5,853-byte bodies (SHA-256
  `088974d65c4378f2e846857cff87ce7d0d21ce77f4267eb827edb4503d2081c7`). This is
  representative local routing evidence, not a full browser or endpoint sweep.
- [x] The intended LAN web at `:5173` and the second instance at `:5174` serve
  byte-identical root pages; both `/aipriceaction-api/health` proxies return
  200 with the same Python API state. The local `:5173` listener is the
  temporary copy launched from the sibling web tree; that checkout remains
  unchanged. A random-UUID Notes GET through the proxy and direct Python
  API/SSR port returned JSON 404 as expected; the web helper's hard-coded
  `127.0.0.1:3000` returned a Next HTML 404, confirming the SSR target mismatch.
  Port 3000 is already owned by a local Next server, so a port-only Compose
  change cannot safely fix Notes SSR without moving that server or changing the
  web helper.

### Compatibility work completed in this pass

- [x] Match Rust's sequential floating-point accumulation and subtraction /
  division order for MA scores, change percentages, JdK momentum, sector means
  and volume-profile statistics; add regression fixtures including values above
  the exact-integer range of `f64`.
- [x] Make all-mode source collision priority deterministic and consistent
  (`vn`, `yahoo`, `sjc`, `crypto`) in Rust and Python.
- [x] Add `sector` to the Rust top-performers query and align Python filtering
  and deterministic tie order.
- [x] Align RRG curated groups and `mascore&trails=0` with Rust's latest joined
  row behavior; do not backfill a missing latest score on the no-trails path.
- [x] Align volume-profile tail selection, rounded inverted-range handling,
  empty-profile body and resource guard; normalize Python inputs to Rust's
  newest-first accumulation order.
- [x] Match the RRG latest MASCORE path's exact 101-row input window for both
  SMA and EMA, keep Rust joined snapshots isolated by raw seed-window depth and
  require a complete symbol set for a hit, and skip empty higher-priority dated
  benchmark vectors in Rust.
- [x] Use the Rust ticker catalog as Python's PostgreSQL-fallback identity
  universe for no-symbol requests, comma-name disambiguation and mode=all
  symbol resolution; cover history-only identity exclusion.
- [x] Make the Python explorer/static bundle byte-for-byte identical to Rust's
  and test the complete file set through the served Python routes.
- [x] Match repeated `sector` query rejection and publish OpenAPI enums, integer
  bounds and the volume-profile mode fallback.
- [x] Add optional `REDIS_URL` support for Rust-compatible `/tickers` per-symbol
  snapshots, joined performer/sector/latest-MASCORE snapshots, bounded raw
  ZSET-tail reads and ticker-list snapshots. `snap=false` skips joined snapshot
  reads but still refreshes them after raw data retrieval; analysis raw Redis
  reads remain enabled as they do in Rust. Fake-Redis tests cover cache
  precedence, malformed/empty/stale snapshots, partial and empty batches,
  freshness, and route forwarding. A worker-to-API test now exercises
  `Worker.sync`, raw cache publication, stale-snapshot invalidation and API
  snapshot refresh using one Redis client. The shared live-Redis HTTP differential now
  compares raw Redis reads across `/tickers`, top-performers, sector MA scores,
  volume profile and representative RRG requests. It explicitly seeds and asserts
  per-ticker and complete joined-snapshot hits for `/tickers`, top-performers and
  sector MA scores. The later paired matrix also checks stale/incomplete/malformed
  per-ticker snapshots, partial/empty batches, ticker-list membership and source
  fallback. Worker restart/rehydration remains open.
- [x] Publish Python worker candle commits to Rust-compatible Redis ZSETs,
  invalidate derived snapshots for changed series, replace retained tails after
  bootstrap/revision completion, and refresh the short-lived ticker identity
  key. Fake-Redis tests verify member shape, range replacement, retention and
  snapshot invalidation. A focused worker/API integration test verifies a valid
  stale snapshot is removed, a worker-published candle is read from the shared
  Redis client, and the API then stores a fresh snapshot. Worker restart,
  bootstrap/revision completion through the worker, and recovery after Redis
  loss or publish failure remain open.
- [x] Align typed snapshot decoding for supported dates: reject duplicate recognized fields,
  non-standard `NaN`/`Infinity` constants, overflowing exponents, unpaired
  surrogates, and `-0` volume tokens; ignore valid unknown fields and normalize
  JSON integers used for f64 fields to floating-point values. Joined timestamps
  follow Chrono's relaxed RFC3339 forms, including leap seconds and non-colon
  offsets. Local regressions and explicit live per-ticker/joined snapshot-hit
  cases pass. Chrono's signed extended years outside Python's 1..9999 datetime
  range remain unsupported.
- [x] Apply signed Redis raw limits before parsing, matching `ZREVRANGE 0,
  limit-1` and end-bounded `ZREVRANGEBYSCORE LIMIT 0,limit`, including the
  distinct zero-limit behaviors and signed `x-redis-limit` diagnostics. Local
  fake-Redis regressions cover malformed oldest members for zero/negative
  limits with and without an end date. The latest paired Rust/live-Redis run
  passes the negative/zero limit cases, malformed-oldest selection, and
  end-bounded embedded-timestamp filtering.
- [x] Match the tested daily SQL-path Yahoo/SJC ticker-name collision before
  indicator calculation, including the shared fixture's EMA200 result and row.
- [x] Match body-limit extractor behavior for declared and chunked bodies:
  Axum's exact chunked JSON rejection text, ignored bodies on unconsumed routes,
  and the static route's exemption from the main-router 5 MiB layer.
- [x] Restrict CORS `Allow` headers to matched method-router paths, distinguish
  JSON integers from floats in differential comparison, and preserve Rust's
  floating-point output types for health and volume-profile values.
- [x] Match static range handling for one leading whitespace after a comma and
  empty-file `bytes=0-` and `bytes=0-0` responses; these use a temporary local
  asset fixture.
- [x] Verify oversized request-body exemption at the exact `/public` mount root
  as well as `/public/*`; the shared matrix includes the mount-root case.
- [x] Fix Rust's dated MASCORE `trails=0` path to return the latest row without
  panicking; the shared HTTP comparison exercises the regression case.
- [x] Keep root `TODO.md` git-ignored; endpoint status and open gates are tracked
  here in `aipriceaction-api/TODO.md`.
- [x] Remove FastAPI-only public documentation routes so Python's public route
  surface matches Rust; keep generated-schema assertions through `app.openapi()`
  and test the Rust-compatible 404 body for every removed documentation path.
- [x] Rerun the shared Rust/Python matrix with GET, HEAD and POST probes for the
  four documentation paths. The paired HTTP matrix test passes these probes
  using explicit disposable PostgreSQL and Redis fixtures.
- [x] Trim all five Vietnamese CSV catalog fields and the ticker lookup key
  with Rust-compatible Unicode whitespace semantics. A local regression test
  uses padded VNZ data around every field. The shared paired `names-vn` case
  pads the existing VCB fields so both APIs must emit the trimmed name without
  changing the fixture's ticker universe. Catalog tests and the paired matrix
  pass after adding the regression.
- [x] Expose response diagnostics used by the web/API clients on configured
  cross-origin responses. Rust and Python expose the same `x-data-source`, Redis
  diagnostics, Cloudflare cache identifiers, rate-limit values, and
  `Retry-After`; the paired matrix compares these CORS headers.

### Read-only production and local web smoke — 2026-10-21

- [x] The local Vite page and its `/aipriceaction-api` proxy returned 200 for
  health, home-page VN/crypto/Yahoo CSV requests, crypto groups, Yahoo names,
  and dated RRG (`2026-09-30`). A repeated top-performers `sector` query returned
  the expected Rust-compatible 400. This verifies the configured proxy path,
  not browser rendering, chart routes or Notes browser workflows.
- [x] Sampled read-only production inputs show data divergence: the same dated
  RRG returned 365 production tickers versus 57 local Python tickers; latest VN
  `/tickers` returned a header-only production CSV versus local rows. Static
  group results matched in sampled modes. This is not algorithm parity proof;
  align data revisions/coverage before comparing numerical outputs.

### Required acceptance gates before claiming 100%

- [x] Pair Redis absence and dependency failures against Rust and Python. The
  isolated four-phase gate passes with Redis absent (18 tests), live Redis
  (full shared-fixture matrix), Redis configured to an unreachable loopback
  port, and ACL-denied Redis (focused snapshot/ZSET and database-fallback
  cases). Python now reports
  `database` when the snapshot connection fails, `redis-snap` when Redis returns
  a per-command snapshot error such as WRONGTYPE but the snapshot batch remains
  reachable, and the same raw Redis diagnostics as Rust for ordinary cache
  reads. Redis recovery after the dependency becomes reachable later in the
  same process remains open.
- [x] Add an opt-in deterministic runner that seeds isolated temporary Rust
  PostgreSQL and Python SQLite stores, requests the shared mounted route matrix,
  and compares status, application headers, JSON shape/values, CSV bytes,
  omissions/nulls and error bodies. The comparator leaves `x-data-source` in
  the comparison and reports byte offset plus first differing line for body
  mismatches. Health compares the complete stable schema and values, validates
  UTC timestamps, and allows only a documented two-second `uptime_secs` startup
  skew. Notes upload, retrieval and deletion are included in the shared
  differential using one disposable session in the isolated Rust working
  directory and Python temporary upload directory.
- [x] Add a PostgreSQL provider preflight that prefers a complete host server
  toolchain and otherwise skips safely by default. An explicit
  `AIPA_USE_DOCKER_POSTGRES=1` opts into a cached `postgres:17-bookworm` image;
  Docker mode binds a generated host port only on `127.0.0.1`, uses a 1 GiB
  tmpfs for PostgreSQL data, generates credentials/database names, rejects
  unexpected mounts, and removes its uniquely named container with `docker rm
  -f` on startup failure or test cleanup. It never pulls an image or uses a
  bind/named volume. Pure tests cover provider selection, command composition,
  mount validation and cleanup command without invoking Docker.
- [x] Rerun the opt-in integration matrix after adding the above-clamp RRG,
  registered no-bars interval, unpadded date, reversed Yahoo/SJC 2W/1M ordering,
  `with_hour=true`, invalid analysis dates, `±Infinity`, mode aliases, lowercase
  ticker-info lookup, UUID canonicalization and volume-profile gap policy
  probes. The paired Rust/Python run passed on 2026-10-21 (1 passed, 8.46
  seconds) using the durable-history fallback with Rust Redis and Python
  cache-tail emulation disabled. The deliberate gap-policy status difference is
  asserted and documented above. The test uses a generated
  loopback port and 1 GiB tmpfs PostgreSQL container, cleans it up on completion,
  and never connects to the shared tunnel.
- [x] Extend the deterministic fixture to 701 weekday sessions ending on the
  latest weekday before the test date; set JDK RRG to the requested
  `mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-20&ema=true`;
  include a VN ticker below that volume threshold plus above-threshold controls;
  add the comma-joined `symbol=FPT,VCB` web request shape; and add a native `1m`
  ticker window beside `1D` and `1h`; add derived intervals, Yahoo/SJC collision,
  selected `mode=all` analysis, and default/invalid/empty cases. The final matrix
  has run against Rust. Notes upload/retrieve/delete run through the same
  differential; `tests/test_notes_contract.py` also checks the Python contract
  independently.
- [x] Align tested `x-data-source` tags for no-Redis database reads and response
  cache hits; Rust's mode=all cache-hit omission is matched by Python.
- [x] Compare static conditional validators, byte ranges and exact trailing-slash
  routing on the same Rust/Python fixture. `If-None-Match` and
  `If-Modified-Since` do not produce Python-only 304 responses; old
  `If-Unmodified-Since` returns 412; range and date `If-Range` return the Rust
  partial representation; `/tickers/` and `/health/` return the Rust 404.
- [x] Extend repeated-scalar rejection and integer-overflow comparisons across
  typed GET routes and Notes query extractors. The paired run found and fixed
  duplicate `secret`/`session_id` differences on Markdown fetch/upload/delete;
  all covered duplicate and overflow cases now pass.
- [x] Extend captured JSON rejection comparisons to malformed numeric and
  Unicode values, nested recursion paths, and empty sequence input; all included
  sync/refresh cases now match status, response headers and exact body.
- [x] Extend the shared matrix with ticker date/interval boundaries, one-sided
  ranges, capped single/multi-symbol limits, full ticker-info output, a literal
  comma-containing ticker that conflicts with two known names, a three-letter
  low-volume VN symbol, and RRG period/trail boundary values. The latest paired
  run passes these probes plus above-clamp RRG, unpadded dates and the registered
  no-bars Yahoo interval.
- [x] Compare static and API `HEAD` responses plus gzip, Brotli, deflate and
  weighted encoding negotiation against Rust. HTTPX decodes compressed bodies,
  so this verifies entity content and headers rather than compressed wire bytes.
- [x] Extend the matrix for Yahoo/SJC collisions on dated and latest 1W
  aggregation, literal comma-containing symbols, full ticker-info output,
  performer sort fallback/zero limit/min-volume overflow, MA invalid-period and
  threshold/NaN values, volume-profile bin/value-area bounds and NaN,
  and RRG period/trail lower and upper boundaries. The latest shared fixture
  passes these cases.
- [x] Extend repeated-scalar and integer-overflow query boundaries across
  typed ticker/catalog/analysis/sync/Notes route extractors. The paired matrix found
  and fixed duplicate optional Notes `secret` on fetch, duplicate `session_id`
  and `secret` on upload, and duplicate delete `secret`; these now return the
  same Axum 400 responses. Overflow probes cover ticker `limit`, performer
  `limit`/`min_volume`, MA period/top-per-sector, profile bins, and RRG
  period/trails/min-volume. The latest paired run passes these cases.
- [x] Add Python unit tests for deliberate top-performer ties and overlapping
  analysis-group ordering. These local tests do not establish Rust/Python
  parity.
- [x] Reject `null` in required OHLC fields for both stock and joined Redis
  snapshots while continuing to allow `null` in optional indicators. New local
  validator tests pass. The paired live-Redis run on 2026-10-21 executes the
  named `rrg-mascore-valid-stale-joined-snapshot` and
  `rrg-mascore-null-required-ohlc-joined-snapshot` cases; both match the Rust
  snapshot/fallback response and expected Redis sentinel. The full run passes
  after the `/raw` SDK-path case was added.
- [x] Add paired live-Redis fixtures for top-performer null metrics and a sector
  MA value with a null MA score. Complete joined snapshots are preseeded for the
  requested VN universe; the comparator asserts the snapshot sentinel and that
  Rust and Python both omit the null-score ticker.
- [x] Add duplicate VCB membership to the shared VN fixture. Rust and Python
  match for JDK and MASCORE RRG; Python deduplicates same-source MASCORE rows to
  match Rust's source/ticker maps while preserving JDK's symbol-list behavior.
- [x] Run the added paired live-Redis RRG seed-depth assertion. The final live-only
  MASCORE case puts 102 same-timestamp duplicates inside the 201-member raw
  window, gives the greatest crawl timestamp a distinct close, and leaves the
  100th-newest outlier outside the selected 99 unique bars. Both servers must
  return the Redis sentinel and scores computed from those 99 bars; the scores
  differ from the result that would include the excluded outlier. The paired
  matrix test passes in 9.17 seconds. The fixture's control check confirms
  MA20 is unchanged while MA100 changes; both Rust and Python return the
  expected 99-bar result. Invalid
  analysis dates, `±Infinity`, the registered Yahoo no-bars interval, RRG
  unpadded dates and above-clamp values, and reversed-identity Yahoo/SJC
  native/2W/1M results pass the shared matrix.
- [x] Expand same-state live Redis differential coverage for stale/incomplete and
  malformed snapshots, partial and empty `/tickers` batches, empty-batch database
  fallback, absent/malformed/empty/subset ticker lists in single-source and
  `mode=all`, and explicit source-priority fallback. A Redis-only FPT tail
  sentinel proves snapshot misses use raw Redis rather than identical SQL rows.
  The paired matrix asserts source tags and Redis diagnostics, and passes in
  9.10 seconds. A separate worker-to-API integration test verifies `Worker.sync`
  updates the shared Redis bars, invalidates snapshots, and serves the new close.
- [ ] Verify cache freshness after worker process restart, Redis loss/recovery and
  database import; Python's worker publishes on successful sync/bootstrap paths,
  but it does not currently prove startup rehydration for already-ready series.
  Production Redis URL, cache state and ticker identity configuration also remain
  deployment gates. The endpoint-level duplicate-member RRG seed-depth assertion
  passed in the paired live-Redis matrix above.
- [x] Match volume-profile behavior when a requested range intersects a recorded
  history gap. The route opts out of only the Python gap-ledger rejection on its
  durable read, retaining pending-repair, revision and basis checks; all other
  history reads remain strict. The shared differential now compares the complete
  HTTP snapshot and verifies 30 available rows and 34,350 total volume.
- [ ] Audit the known VCB data divergence before any numerical parity claim. A
  September 3, 2026 comparison found 519 of 600 OHLC rows differed between Rust
  production and local Python over April 4, 2024–May 12, 2026, while timestamps
  and volumes aligned; older coverage also differed (911 Rust rows versus 725
  Python rows). Treat this as underlying revision/coverage divergence, not a
  calculation fix, until matching input revisions are compared.
- [x] Verify the LAN wrapper routes the user's supplied homepage/API URL set to
  Python. After rebuilding API and worker together, all 17 unique requests
  returned 200 through `http://192.168.1.13:5174`: root, `/chart`, health,
  VN/crypto/Yahoo homepage CSV with MA+EMA and without MA, the VN 1D/EMA request,
  group/name variants, and dated JDK RRG. The 2026-10-20 RRG request took 10.14s.
  The temporary Vite copy binds `0.0.0.0:5174` because 5173 is occupied; the
  sibling web repository was not edited.
- [ ] Verify browser rendering, chart-data presentation, and Notes upload/view/
  delete end to end through the active SPA path. The packaged Explorer also sends
  `with_hour=true` for its “Daily + Hourly” option, but both servers currently
  return `hourly:null`; decide whether to implement that advertised feature or
  remove the control and document the limitation.
- [x] Make `scripts/serve_local_web.py` derive its default API target from
  `AIPA_LOCAL_API_URL`, `VITE_API_PROXY_TARGET`, process `AIPA_API_PORT`, then
  this project's `.env` `AIPA_API_PORT` (Compose defaults to 8899). The wrapper copies the sibling web tree to a
  temporary directory and does not edit it.
- [ ] Verify direct Vite startup separately from the LAN wrapper. The sibling
  web repo's `vite.config.ts` defaults `VITE_API_PROXY_TARGET` to port 3000, so
  ordinary `npm run dev` still does not inherit Compose's 3002 mapping. Keep
  the web repository read-only.
- [ ] Verify the exposed response-header list in a real cross-origin browser.
  The paired HTTP matrix checks the configured CORS response, but only a browser
  request from the production web origin proves JavaScript can read headers
  through the deployment edge.
- [ ] Add a `.br` static asset fixture if deployment begins shipping precompressed
  Brotli files. The current packaged asset bundle has no `.br` files; dynamic
  Brotli compression is covered by the Rust/Python matrix.
- [x] Audit `AIPriceActionClient.getRawData()` and the deprecated Explorer note
  pointing to `/raw/{path}`. The web SDK still exposes this deprecated method,
  but no active UI call site uses it and neither Rust nor Python mounts the
  route. Add `GET /raw/ticker_group.json` to the paired matrix; both return the
  same 404 status, headers and body. This records parity while keeping the
  retired proxy unavailable in both backends.
- [ ] Verify Notes upload, view and deletion in a browser. The active app uses
  the browser API base; the server-side helper branch is dormant. Direct Vite
  startup may target port 3000, while `serve_local_web.py` overrides it to the
  configured API port. Keep the sibling web repository read-only.
- [ ] Verify cold/concurrent and production-equivalent RRG latency. A read-only
  warm-cache benchmark against local Python returned the same payload hash on
  all three samples (215,307 decoded bytes; SHA-256
  `052af13a73fd9f5fb4c7e7fe34c9624737fe5e73b2b292795ef3918a3c6cc0fe`), with
  p95 10.23s against a 25s target. The exact 2026-10-20 request through the web
  wrapper took 10.14s. These local warm results fit the web's 30-second abort;
  cold, concurrent and production-equivalent behavior remain open. The servers'
  query timeout remains 180 seconds.
- [ ] Inventory private sync records and verify refresh/sync authorization,
  migration, and error behavior against the actual deployment configuration.
- [ ] Verify the production launch configuration has the intended CORS origins,
  refresh secret and sync bearer token. Exercise both authorized and rejected
  requests against the candidate before public routing changes.
- [ ] Expand the JSON rejection corpus to remaining unusual header-byte and
  malformed escape/number ordering combinations. The current captured body
  corpus already covers invalid UTF-8, numeric syntax, recursion, sequences,
  duplicate/missing fields, Unicode surrogate errors and trailing data.

### Validation performed — 2026-10-21

- [x] Latest successful shared HTTP differential passed after adding the chunked-body,
  static body-limit and `/public` mount-root, `OPTIONS` fallback, typed JSON
  scalar, static range, dynamic/static HEAD compression and Yahoo/SJC 1W cases.
  The latest rerun also verifies recursive JSON key order, empty Yahoo 1h
  omission, invalid top-performer/volume-profile dates, `±Infinity`,
  unpadded/invalid RRG dates, above-clamp RRG values, reversed-ID 2W/1M
  Yahoo/SJC collisions, `with_hour=true`, populated Notes symlink rejection
  and a volume-profile partial response that overlaps the history-gap ledger.
  The durable-history pair used a unique loopback-bound PostgreSQL container on
  tmpfs and a temporary Python SQLite database, both cleaned after the run. The
  expanded live-Redis rerun passes in 7.49 seconds after adding latest RRG,
  MA-mode, sort-key and cross-market volume-profile cases. It also asserts seeded
  per-ticker and joined-snapshot hits with null indicators. A later live-Redis
  rerun adds stale/incomplete/malformed per-ticker snapshots, divergent Redis-only
  fallback bars, ticker-list fallback/source-priority cases, and partial/empty
  batch diagnostics; it passes in 9.10 seconds after fixture-state isolation. The
  worker-to-API freshness test passes in the focused Redis publisher suite (4
  passed in 2.10 seconds). The post-change focused regression set passes 258
  tests with one opt-in integration test skipped in 27.32 seconds. These checks do not
  verify worker restart rehydration, production cache configuration, or browser
  behavior.
- [x] The matrix checks status, application/CORS/cache headers, JSON field
  shape, scalar types and recursive object-key order, CSV bytes, omissions/nulls
  and error bodies. It allows only documented health clock/uptime variance and
  deployment-derived static `Last-Modified`; HTTPX decompresses responses before
  comparing bodies. Raw JSON whitespace, number spelling and compressed wire
  bytes are not compared.
- [x] Rerun the full opt-in paired HTTP matrix after the adversarial route audit
  and explicit legacy SDK `/raw` case: `AIPA_RUN_RUST_DIFFERENTIAL=1
  AIPA_USE_DOCKER_POSTGRES=1 AIPA_USE_LIVE_REDIS_DIFFERENTIAL=1 uv run pytest
  -q tests/test_rust_python_differential.py` passes **14 pytest functions in
  11.75s**; one function executes the paired HTTP matrix and the others test
  harness helpers.
  The run uses a unique loopback-only PostgreSQL container on 1 GiB tmpfs, a
  nonpersistent loopback Redis process, and temporary Python SQLite/archive/
  upload paths. It includes the exact homepage CSV and JDK RRG request shapes,
  chart ticker daily/hourly/minute reads, catalogs, info, analysis, health,
  Notes upload/read/delete, sync, CORS, and static routes. It also executes the
  live-Redis valid-stale and null-required-OHLC RRG cases. The temporary test
  resources are stopped and removed by the harness. This proves same-fixture
  contract parity for the matrix; it does not resolve live data-revision,
  browser/SSR or production-configuration gates above.
- [x] Earlier opt-in paired live-Redis run expanded analysis cache branches:
  complete and incomplete `mode=all` joined snapshots for top performers, MA
  scores and latest MASCORE RRG; stale top-performer snapshots; partial minute
  tails that overlap the requested date; and non-overlapping tails that must
  fall back to SQL. The missing Yahoo snapshot case removes both its joined
  snapshot and raw Redis bars, verifying source-local fallback. The paired HTTP
  matrix test passed in 11.27s. Its fixture used distinct same-source scores; the later
  adversarial run added equal-score cases after deterministic ordering was
  implemented. The temporary PostgreSQL container, Python SQLite/archive/upload
  paths, and nonpersistent Redis process were cleaned; local API data remained
  6.0G and RustFS remained 5.1G.
- [x] Disable FastAPI's default public docs/OpenAPI routes. The local route and
  schema tests pass (4 passed); schema assertions now call `app.openapi()` and
  each of `/docs`, `/redoc`, `/openapi.json`, and `/docs/oauth2-redirect` returns
  the Rust fallback body. Ruff and `git diff --check` pass. The paired matrix
  passes the GET, HEAD and POST probes for those paths.
- [x] Historical validation before the latest full-suite run included a
  1,364-pass/one-skip Python run, a contract-file run with 170 passes and three
  failures caused by an outdated test-double signature, and separate passes for
  those three corrected cases. Pending-repair endpoint cases passed (4), and
  history/public-history tests passed (45). Those counts predate the later full
  suite and Redis boundary fixes; they are retained as run history, not as the
  current acceptance result. The current post-fix evidence is recorded above.
- [x] `ruff check src tests scripts`, formatting checks for changed Python
  files, and `git diff --check` pass. Repository-wide `ruff format --check`
  still reports six untouched files with pre-existing formatting differences.
- [x] The complete Python suite passes 1,502 tests with one skipped in
  399.51 seconds; the previous run's stale UTC-suffix expectation was corrected
  and verified by this clean run. Rust tests pass 20/20 and Python analysis
  compatibility tests pass 52/52. The latest rebuilt Rust/live-Redis paired
  endpoint matrix test passes in 12.50 seconds, including independent checks
  that HEAD matches its GET headers and Content-Length for a populated one-row
  FPT CSV, DELETE preflight allowance, date-bound behavior, and overlapping-group
  labels. Ruff, scoped formatting, compilation and
  `git diff --check` pass. The isolated paired PostgreSQL container was removed;
  local API data remained 6.0 GB and RustFS remained 5.1 GB. No persistent
  differential container or database copy was retained.
- [x] Protect the shared API/worker Parquet cache with stable per-checksum
  `flock` read pins and nonblocking eviction locks. Cross-process eviction retries
  use a shared marker published under the eviction lock before atomic cache
  replacement; this covers failed reads and prevents another process from
  clearing the marker before the new file appears. Ten focused lock tests pass,
  including the actual read/eviction race, same-checksum materialization,
  failed-read cleanup and marker-clear interleaving. A cross-container lock
  rehearsal on the Compose mount passed. API and worker were rebuilt and
  recreated together from the same image; both are healthy. A process killed
  during download can still leave an uncounted `*.tmp` file in the cache; this
  pre-existing crash-cleanup edge is outside the Parquet byte-budget accounting.
- [x] After the latest full run, `aipriceaction-api/data` remains 6.0G and
  the live RustFS `/data` volume remains 5.1G. The API container remains 1.2G
  with a 14M archive cache and 3,148 Parquet files. The paired PostgreSQL container
  is gone. No new test database copies were created in the persistent data
  directory; its older SQLite sidecars remain untouched as retained evidence.
  Temporary differential databases use temporary storage and are cleaned on exit.
- [x] After signed-limit, malformed-snapshot, null-indicator and MASCORE
  deduplication and typed-cache-boundary fixes, the focused Redis, snapshot and
  analysis suite passed 148 tests; Ruff and formatting checks pass for the changed
  files. The expanded Redis-enabled paired rerun passed one matrix test in 7.49
  seconds after correcting Yahoo MA-sector membership and volume-profile floating
  point operation order. Its isolated PostgreSQL tmpfs container and loopback
  Redis process were cleaned up. The current full Python suite result is recorded
  above.
- [x] Current focused validation after required-OHLC snapshot rejection and the
  RRG seed-depth fixture: 120 snapshot/Redis/analysis tests pass; 13 local
  differential-harness checks pass; Ruff, compilation and `git diff --check`
  pass. The first paired run exposed an incorrect fixture self-check: it
  expected both MA20 and MA100 to change, although the excluded oldest bar is
  outside MA20. The check now asserts the intended MA20 invariance and MA100
  divergence. The escalated paired Rust/Python matrix test then passed in 9.17
  seconds, including the live-Redis seed-depth case. A follow-up matrix test
  passed in 10.51 seconds after adding padded VCB catalog fields
  and exposed CORS diagnostics. Adversarial reviewers independently confirmed
  raw-limit selection, highest-crawl winner, exact score expectations and case
  isolation. No permanent PostgreSQL container or database copy was retained.

- [x] Latest adversarial endpoint rerun used the rebuilt Rust binary and live
  Redis and passed the paired HTTP matrix test in 12.50s. It adds equal-score ordering
  in top-performers and sector scores, invalid typed modes, CSV numeric-format
  boundaries, `HEAD /tickers`, volume-profile bound precedence and one-sided
  bounds, the Notes DELETE CORS preflight, and overlapping-group requests for
  top-performers and JDK RRG. Independent assertions require each server's HEAD
  response to match a populated one-row FPT CSV GET, including Content-Length,
  successful empty preflight with DELETE allowed, date-only equivalent profiles,
  and every duplicated FPT row to use the `Alpha` sector. The clean full Python
  suite passes 1,502 with one skip.
  Temporary PostgreSQL, Redis and test directories were removed; API data stayed
  6.0G, RustFS stayed 5.1G, and no differential PostgreSQL container remains.

- [x] Rebuilt the current checked-out Rust server and reran the required gate
  with `./scripts/check_rust_python_parity.sh`; all 14 pytest functions passed
  in 17.02s, including the paired HTTP matrix with live Redis. The matrix now
  independently validates home CSV row count/schema/date/close/MA values,
  refresh schedule side effects for four modes by three intervals, production
  web-origin CORS and JSON preflight, and static GET/HEAD/POST/PUT behavior
  with both populated and missing public assets. The full API contract test
  file then passed 177 tests in 313.72s, including the static regressions. The
  adversarial route audit exposed and fixed Python's missing `Allow: GET,HEAD`
  on static 405 responses
  and its missing-directory fallthrough; paired cases now cover both. Focused
  static regression tests pass (2). The gate uses loopback Redis and an isolated
  temporary PostgreSQL container; its cleanup removed both. Post-run checks
  found no differential PostgreSQL container; `data/` remained 6.0G and
  RustFS `/data` remained 5.1G. The web checkout remains read-only. Actual
  browser parsing/rendering, Notes browser workflows, production state/configuration, and
  live data/catalog equivalence remain open.

Until these gates pass, describe the work as compatibility improvements with
documented differences, not a 100% drop-in replacement.

### Latest implementation validation — 2026-10-21

- [x] Fix dated aggregate storage-path parity at both coverage boundaries.
  Rust and Python PostgreSQL fallbacks now read the same retained raw-candle
  context as Redis, calculate aggregate candles and indicators, then filter by
  aggregate bucket timestamps. Python only treats a range as Redis-covered when
  an actual Redis client confirms coverage; without Redis, both implementations
  preserve bounded SQL aggregation and its partial first bucket. Historical
  ranges outside Redis coverage retain their bounded SQL query. Focused date-
  window and weekly-indicator regressions cover both behaviors; the paired
  matrix checks the covered Redis/forced-PostgreSQL path and the no-Redis path.
- [x] Protect response-cache identity for comma-bearing symbols. Rust now
  encodes its sorted symbol array unambiguously in the key and has a unit test
  for the literal `DUPL,FPT` symbol versus repeated `DUPL` and `FPT` inputs.
  Both request orders are in the paired matrix. The independent payload-key
  assertions require each request to return its own symbol selection and reject
  an `in-memory` hit from the distinct request's cache entry.
- [x] Extend independent analysis checks: JDK RRG formula assertions cover
  every returned ticker row, raw relative strength, momentum, volume and
  zero-trail behavior. The `mode=all` matrix independently checks Yahoo and
  crypto benchmark fallbacks plus VN-over-Yahoo priority for `DUPL`, which has
  distinct fixture histories in both sources. Volume-profile success and the
  no-minute-history 404 body are independently checked; the exact widget shape
  (`bins=10`, default 70% value area) is checked for VN, crypto and Yahoo.
  Top-performer Yahoo/crypto sector expectations follow the Rust VN-group lookup.
- [x] The paired Rust/Python endpoint gate passes in four configurations:
  18 tests with Redis absent, the live-Redis matrix, the unreachable-Redis
  matrix, and an ACL-denied Redis matrix. Its new multi-symbol outage request
  requires both VCB and FPT in the response, `x-data-source: database`, and no
  Redis diagnostic headers. The ACL case allows connection and DB selection
  but denies snapshot and OHLCV commands, so it exercises NOPERM fallback in
  both clients. WRONGTYPE remains a per-command snapshot miss, matching fred's
  error mapping. The full Python suite passes **1,528 tests with one skipped**;
  after the final Redis error-classification changes, the focused Redis client
  and route suite passes **31/31**. `SQLX_OFFLINE=true cargo test -q` passes
  **22/22**. Ruff, Python compilation, and `git diff --check` pass. The paired
  matrix also checks the signed no-MA SQL empty response, zero-retention Redis
  limits, snapshot recovery, and the independent long-history aggregate oracle.
- [x] Harden Rust health-count initialization for live writers. Trigger
  installation commits separately from the count reconciliation; the
  reconciler commits each ticker baseline independently under the same advisory
  lock used by its trigger. Existing tickers are marked unreconciled so writes
  skip count deltas until that ticker's exact baseline commits; newly created
  identities initialize safely on their first write. A populated PostgreSQL
  fixture reruns reconciliation and verifies a pre-reconciliation delete plus
  insert, all three intervals' insert/delete/update paths, and moving a candle
  between tickers and intervals. This avoids a full-history scan while holding
  a write-conflicting table lock. Direct `TRUNCATE` of OHLCV still bypasses row
  triggers; no current application path uses it, and manual maintenance must
  run `CALL reconcile_ohlcv_health_counts()` afterward.
- [x] Align initial health refresh retry behavior and cache-fallback source
  tags. Python immediately retries a failed startup snapshot query, preserving
  the last complete snapshot after later failures. Redis fallback tags now
  match Rust for transport failures, generic/auth/permission errors, and
  WRONGTYPE per-command misses. The paired live-Redis fixture checks an
  all-WRONGTYPE snapshot batch with valid ZSET fallback; unreachable and
  ACL-denied phases verify full database fallback for one and multiple tickers.
- [x] Propagate major-ticker lookup failures from the Rust health refresh so a
  transient query error cannot publish empty sync timestamps over the last
  complete snapshot. Production-scale counter-trigger throughput remains an
  explicit pre-cutover benchmark gate.
- [x] Test cleanup leaves `aipriceaction-api/data` at 6.0G and the live RustFS
  data volume at 5.1G, unchanged across the final four-phase gate. No
  differential containers or full database test copies remain. Root `/TODO.md`
  is ignored by `.gitignore`; this tracked endpoint roadmap remains
  `aipriceaction-api/TODO.md`.

These local contract checks do not close the outstanding production-data and
deployment gates above. Production and local VCB historical rows diverge,
browser parsing/rendering and Notes flows are not accepted end-to-end, private
sync records and production runtime settings remain unverified, and the sibling
web checkout still has a conditional literal-comma CSV parsing gap. Do not claim
100% compatibility until those differences are resolved or their inputs are
formally excluded.

### Final verification update — 2026-10-21

- [x] Re-run the full Python API suite after the Redis URL parsing updates:
  1,568 passed, 3 skipped, in 427.20 seconds. Ruff, Python compilation and
  `git diff --check` pass. The four-phase Rust/Python gate also passes after
  the final changes: 20 tests with Redis absent, then one shared-fixture test
  each with live, unreachable and ACL-denied Redis.
- [x] Compare every URL from the home-page checklist through the local API and
  LAN web proxy. All 19 requests returned HTTP 200 at both
  `127.0.0.1:3002` and `192.168.1.13:5173/aipriceaction-api`; CSV, group, name
  and RRG bodies matched byte-for-byte. Health's stable fields match; only
  `current_system_time` and `uptime_secs` advanced between the two requests.
  This verifies the proxy path and local API response, not browser rendering or
  deployed production data.
- [x] Align centralized `REDIS_URL` edge parsing with Fred for missing hosts,
  invalid UTF-8 percent-encoded credentials, URL edge whitespace, and Valkey
  aliases. The repository's production Rust Compose configuration uses a
  standalone `redis://` endpoint. Cluster and Sentinel URLs remain unsupported
  by Python and must not be used for the Python deployment without a separately
  tested topology-aware client.
- [x] Compose validation passes when a non-empty `REFRESH_SECRET` is supplied;
  it intentionally rejects an unset/empty secret. Root `/TODO.md` remains
  ignored by `.gitignore`.
- [x] The parity run left no `aipa-differential` containers. Local API data is
  still 6.0G and the existing RustFS data volume is still 5.1G; no temporary
  database or object-store test data is retained.

Production VCB history, production runtime settings and the sibling web
client's literal-comma CSV parser remain open compatibility gaps. Keep the
100% drop-in claim blocked until their inputs are compared or formally excluded.

### Adversarial endpoint and web-consumer review — 2026-10-21

- [x] Re-run the paired endpoint gate after adversarial review: 20 checks pass
  with Redis absent, followed by passing shared-fixture checks with live Redis,
  unreachable Redis, and ACL-denied Redis. The request matrix now pins negative
  unsigned query values, malformed typed JSON followed by later syntax errors,
  and the volume-profile resource-limit response body.
- [x] Fix the confirmed low-priced crypto `/analysis/volume-profile` mismatch.
  Python now uses Rust's first profile level within `$0.01` of the POC when
  seeding value-area expansion. The MICROUSDT paired fixture explicitly expects
  POC `0.0002`, value area `0.0001–0.0002`, and volume `101`.
- [x] Fix malformed JSON field context for typed string fields so Python's
  rejection text includes Serde's field path for malformed escapes and raw
  control characters. Refresh and sync malformed-body cases compare those
  responses against Rust.
- [x] Add a paired WIDEUSDT case that reaches the 1,000,001-step boundary and
  independently checks the exact HTTP 400 resource-limit error body.
- [x] Correct the Python SDK's interval documentation: it does not implement
  calendar-month `1M` aggregation end to end, despite prior docstrings claiming
  support. The supported SDK aggregate intervals are `5m`, `15m`, `30m`, `4h`,
  `1W`, and `2W`; 1M support remains a separate feature gap.
- [x] Run the complete Python API test suite after the adversarial fixes:
  **1,568 passed, 3 skipped**. `git diff --check` is clean.
- [x] Verify test cleanup after the full run: no `aipa-differential` containers
  or volumes remain; API `data/` is 6.0G and RustFS `/data` is 5.1G, unchanged
  from the recorded pre-run sizes.
- [x] Adversarially inventory mounted Rust routes, methods, static paths, CORS
  probes, and query edges. A one-off 512-case request-builder sweep was run,
  but its generator and result log were not retained, so that sweep is
  transient evidence. The checked-in route inventory in
  `tests/test_api_contract.py` reproducibly covers active Rust/Python route
  registration and mounts. Neither inventory replaces the paired runtime
  matrix or browser acceptance checks.

Adversarial web review found no active route or method registration mismatch,
but the external web checkout still has unclosed consumer-contract issues:

- Its CSV parser splits literal commas and drops the synthetic fixture
  `DUPL,FPT` symbol row even though Rust and Python return matching unquoted CSV. The
  relevant file is `/Users/quan/data-workspace/aipriceaction-web/src/integrations/aipriceaction/src/utils.ts`.
- Its SDK sends `format: "JSON"` to the CSV parser even though both APIs accept
  the format case-insensitively. It also rejects bins/value-area and RRG bounds
  that both APIs intentionally clamp, and its response/query types omit fields
  present in the API contract.
- The packaged Explorer defaults to an hourly option and sends
  `with_hour=true`, but Rust ignores this unknown query key and returns
  `hourly: null`; Python now has paired coverage for true, false, invalid and
  duplicate values, including unchanged response JSON versus the baseline.
  Explorer documentation promises hourly results but describes a shape that
  conflicts with Rust's response scaffold and leaves timezone, hour labels,
  ranking, and indicator baselines undefined. Keep server parity at Rust's
  current behavior until those semantics are defined and implemented in both
  services. Rendered browser/Notes acceptance and deployed-edge checks remain
  outstanding.
- Production database/history and runtime configuration have not been proven
  equivalent to the local fixtures. Stale production results cannot establish
  compatibility by themselves; repeat with a known synchronized date and the
  deployed revision before cutover.

The sibling web checkout remains read-only per the user's instruction. These
items therefore remain blockers to claiming 100% web-to-API compatibility even
when the paired Rust/Python server matrix passes.

### Latest parity verification — 2026-10-21

- [x] Match Rust's compact JSON float spelling across app and upload routes.
  The response encoder now follows serde_json's fixed/scientific notation
  thresholds; the paired matrix compares JSON wire bytes, including small
  `std_deviation` values that previously differed only by exponent notation.
- [x] Tighten the parity comparator so volatile health/sync fields cannot hide
  malformed JSON, duplicate volatile keys, non-decimal `Content-Length` values
  (including compressed responses), identity-body length mismatches,
  encoded-length presence mismatches, or HEAD/GET header errors. Every HEAD
  request now compares its matching GET representation across both runtimes.
- [x] Correct the i64 limit, performer-volume, RRG-volume and profile-boundary
  independent oracles. The profile fixture now reaches the requested UTC date,
  covers over 200 price ticks, and exercises bins 0/2/200/201/u64::MAX and
  value-area 0/60/70/90/100 clamps. The MICROUSDT hard-coded case freezes
  Rust's one-cent POC-neighborhood lookup behavior as a compatibility quirk.
- [x] Run the four-phase Rust/Python gate after the final Notes route and
  extractor-precedence adjustments: 34 tests pass with Redis absent (40.10s);
  the shared-fixture parity case also passes with live Redis (32.29s),
  unreachable Redis (49.16s), and ACL-denied Redis (4.05s). Temporary
  PostgreSQL/Redis resources are removed.
- [x] Run the full API suite after the response-encoder change:
  **1,589 passed, 3 skipped** in 429.80 seconds. Ruff, Python compilation and
  `git diff --check` pass.
- [x] Verify cleanup after the final four-phase run: no
  `aipa-differential` containers or volumes remain; local API `data/` is still
  6.0G and RustFS `/data` is still 5.1G. The root `/TODO.md` remains ignored by
  `.gitignore`.
- [x] Make both analysis clamps observable with 110 eligible synthetic symbols
  in an isolated request-scoped sector. `limit=usize::MAX` returns exactly 100
  top and 100 worst rows; `top_per_sector=usize::MAX` returns exactly 50 of
  the 110 sector rows. The no-Redis Rust/Python matrix passed with these
  independent cardinality assertions.
- [x] Add a lower RRG clamp oracle: `period=3` returns period `4` and the
  independent JdK calculation uses that effective value.
- [x] Close adversarial Notes route and storage boundaries. Rust now raises
  Axum's multipart extractor limit to the route's 5 MiB file allowance plus
  framing; paired tests accept exactly 5 MiB, reject 5 MiB plus one byte, and
  verify the stored and fetched content digests. Python preserves Axum's raw
  route matching for encoded session separators, encoded fixed `markdown`
  segments, empty and nested filenames, DEL/C1 controls, and duplicate-query
  extractor precedence. Paired tests also accept an aggregate upload exactly
  at the 6 MiB quota and reject the next byte. All quota filler files live in
  the differential test's temporary directory and are removed after each case.
- [x] Probe the checked-out TypeScript SDK with an injected `fetch` while keeping
  the sibling web checkout read-only. The actual SDK sends uppercase
  `format: "JSON"` and then treats the mocked JSON object as CSV (`.trim` fails);
  lowercase `json` resolves. It rejects unpadded dates that both APIs accept,
  malformed optional dates that the API ignores or falls back from, and RRG /
  volume-profile bounds that the APIs clamp, all before sending a request. Its
  literal-comma CSV parser drops the registered `DUPL,FPT` row. For
  end-date-only volume-profile requests, the SDK also rejects before sending,
  while both servers return HTTP 400 with the required-date error; the API
  acceptance boundary matches, though the client surfaces a local validation
  error instead of an HTTP response. These probes confirm client-side behavior,
  not full browser or real-network acceptance.
- [ ] Fix the SDK parsing and validation gaps above and complete browser
  acceptance. The sibling web checkout remains read-only under the user's
  instruction, so its files have not been changed.
- [ ] Verify browser chart/analysis/Notes flows, cross-origin header access, and
  production data/runtime settings. The active web client still has a
  literal-comma CSV parsing issue, `with_hour=true` still returns `hourly:null`,
  and its query/response types omit supported fields. Volume-profile's intended
  daily fallback is unreachable when the API returns 404 for missing minute
  data. Vite and the Notes SSR loader default to port 3000 while the local API
  proxy has used 3001; Notes upload/delete also ignore the configured API URL
  override. The web checkout remains read-only and unchanged.

- [x] Recheck storage after the four-phase gate: no differential test
  containers or full-database temporary directories remain; `aipriceaction-api/data/`
  remains 6.0G and RustFS `/data` and `/logs` remain 5.1G and 26M. The ignored
  root `TODO.md` is still covered by `.gitignore`.
- [x] Re-run the four-phase gate after adversarial review corrected the
  `with_hour` oracle. All 34 no-Redis cases pass (40.10s), followed by the
  shared-fixture case with live Redis (32.27s), unreachable Redis (49.09s),
  and ACL-denied Redis (3.98s). The four query variants now require HTTP 200,
  the `top_performers` envelope, an explicit `hourly: null`, and response-body
  bytes identical to the no-`with_hour` baseline for each server. Ruff, Python
  compilation, and `git diff --check` pass. No differential containers or
  volumes remain; API `data/` is 6.0G and RustFS data is 5.286GB.

These results establish broad same-fixture Rust/Python server parity. They do
not establish 100% compatibility with the external SDK, rendered SPA, or
deployed production state. The HTTP comparator compares decompressed response
bodies and normalized header mappings, so it does not prove compressed wire
bytes, duplicate-header multiplicity/order, or Uvicorn/Axum framing behavior.

### Adversarial follow-up — Explorer and homepage proxy replay — 2026-10-21

- [x] Re-audit the 15 distinct homepage URL shapes from the user’s list through
  the direct Python API (`:3002`), the local Vite proxy (`:5173`) and the local
  wrapper (`:5174`). All 45 requests returned HTTP 200. Every ticker, group,
  name and RRG response matched the direct API body hash through both proxies;
  health matched after excluding its expected request-time and uptime fields.
  The duplicated URLs in the original list were represented once per distinct
  request shape. This verifies local routing and Python response delivery, not
  Rust-vs-production values or a rendered external page.
- [x] Fix the packaged Explorer’s JSON record count to sum candle arrays in the
  API’s symbol-keyed object. Remove the unsupported “Daily + Hourly” request
  option, which sent `with_hour=true` even though both servers return
  `hourly: null`. Add explicit market, EMA and snapshot controls to the packaged
  top-performers and MA-by-sector forms so those query fields can be exercised.
  The RRG form also exposes its supported EMA and snapshot options, including
  the user's `ema=true` request shape.
  The Rust and Python public bundles remain byte-identical. Node VM checks
  verify that the forms construct the selected mode/EMA/snapshot query and that
  the ticker JSON count displays the actual number of candle rows.
- [x] Correct the packaged API reference for the symbol-keyed `/tickers` JSON
  shape and its mode/Redis/MA/EMA/snapshot fields, mode aliases for catalog and
  analysis routes, health response field names, analysis response envelopes,
  supported RRG algorithms, and RRG’s actual default minimum volume.
- [x] Add the optional `secret` query field to Python Markdown retrieval’s
  OpenAPI signature. Rust accepts but ignores it for Markdown; both runtimes
  serve public notes and deny private notes regardless of whether the query
  secret is absent, wrong or correct. Tests pin the optional OpenAPI parameter,
  public retrieval with the query, and the private-session denial behavior.
  A query-field inventory test also compares each of the eight static Rust
  query-field manifests against the generated FastAPI OpenAPI parameters.
- [x] Run the complete Notes contract file plus the OpenAPI query-inventory,
  Explorer asset, and mode/schema regressions: 15 passed. Scoped Ruff checks,
  formatting, JS syntax, `git diff --check`, and root TODO ignore checks pass.
  The Node VM runtime check also passes for JSON row counting and all three
  analysis URL builders. Root `/TODO.md` remains ignored by `.gitignore`.
- [ ] Browser acceptance remains open. The cached Playwright package could not
  launch system Chrome and timed out after 180 seconds; a Node VM check covers
  the changed browser JavaScript but does not replace a rendered browser test.
  The sibling web checkout remains read-only. Its checked-in Vite proxy defaults
  to port 3000, while the active `:5173` process passed the same-body proxy
  replay and therefore has an effective override. The sibling TypeScript SDK
  still has parser/type gaps recorded above. Production history and runtime
  parity also remain open.

### Final compatibility validation — 2026-10-21

- [x] Correct the packaged API reference and general health-panel copy in both
  Rust and Python bundles:
  `/tickers/group` returns curated group-to-symbol maps, not interval
  availability; `/health` cache/memory fields are documented as zero-valued
  placeholders; MA grouping documents VN groups and the additional Yahoo/crypto
  groups used by `mode=all`; RRG documents JDK-only benchmark/period fields and
  conditional `source`/`trails` fields. Both `index.html` copies are
  byte-identical and `git diff --check` passes. The general health panel no
  longer promises cache performance metrics.
- [x] Re-run the packaged asset parity test after the final health-panel copy
  correction: 1 passed.
- [x] Run the complete Python API suite after the Explorer, Notes/OpenAPI, and
  reference changes: **1,594 passed, 3 skipped** in 431.91 seconds. The only
  warning is Starlette's existing `httpx` TestClient deprecation notice.
- [x] Run `scripts/check_rust_python_parity.sh` using the existing local
  Colima Docker context. The no-Redis run passes 34 cases; shared-fixture runs
  pass with live Redis, unreachable Redis, and ACL-denied Redis (one case per
  Redis phase) against a Rust executable rebuilt from the current checkout.
  Temporary PostgreSQL containers/volumes are absent afterward.
  API data remains 6,261,008 KiB and RustFS `/data` remains 5,267,428 KiB;
  `/logs` measures 25,520 KiB on the final read-only check. No pytest SQLite
  databases or sidecars from the run remain in the inspected temporary
  directories.
- [x] `cargo build --locked --offline` succeeds. `SQLX_OFFLINE=true cargo test
  --locked --offline` passes all 22 Rust unit tests without connecting to a
  database. Repository-wide `cargo fmt --check` reports formatting drift across
  multiple files; no blanket reformat was applied.
- [ ] Rendered-browser acceptance is still open: no local Chrome executable is
  available, and the prior Playwright launch timed out. The sibling web
  checkout remains read-only. Its SDK CSV parsing/uppercase-format validation
  gaps and the checked-in Vite/Notes port overrides remain unresolved. Browser
  behavior and revision-pinned production data/runtime parity are not certified;
  do not claim 100% end-to-end web compatibility or cut over.
