# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.14] - 2026-05-12

### Fixed
- Use live data as fallback when S3 has no OHLCV data for the requested date range — `get_ohlcv()` previously returned an empty DataFrame before reaching the live overlay code

## [0.1.13] - 2026-05-11

### Added
- `performers` module: `build_performers()` function and `PerformerInfo` dataclass for top/worst performer ranking from live daily data (port of Rust `performers.rs`), with `value` field (close × volume) and `value` sort option
- `volume_profile` module: `compute_volume_profile()` function and `VolumeProfileResult` dataclass for volume-by-price histogram analysis with POC, value area, and statistics (port of Rust `volume_profile.rs`)
- Both modules are pure Python with no external dependencies beyond pandas for volume_profile input

## [0.1.12] - 2026-05-11

### Added
- Add `ma` parameter to `fetch_live_data()` (default `True`) to control whether backend computes MA scores, reducing response size and timeout risk for aggregated intervals
- Add client-side OHLCV aggregation module (`aggregator.py`) supporting non-native intervals: `5m`, `15m`, `30m`, `4h`, `1W`, `2W`
- `get_ohlcv()` now supports all intervals (native + aggregated) — base data is fetched and aggregated client-side
- Expand live data request limits for aggregated intervals (`5m: 12`, `15m: 4`, `30m: 2`, `4h: 6`, `1W: 4`, `2W: 2`)

### Fixed
- `get_ohlcv()` now passes `ma=False` to `fetch_live_data()` since MA is computed locally after merge — avoids wasted bandwidth
- Increase live request timeout from 5s to 15s to handle larger responses
- Fix test that expected `ValueError` for aggregated intervals (now supported via aggregator)

## [0.1.11] - 2026-05-11

### Fixed
- Filter live data snapshot by source using ticker metadata from `tickers.json` instead of USDT suffix heuristic, which leaked global/yahoo tickers into VN snapshots
- Handle missing `final_report` in `multi_agent.py` example when reviewer never approves after max rounds, falling back to last aggregator output

## [0.1.10] - 2026-05-10

### Fixed
- Sort OHLCV data chronologically in `get_ohlcv()` when yearly files are fetched in reverse year order, so `tail(limit)` returns the most recent rows instead of the oldest

## [0.1.9] - 2026-05-09

### Fixed
- Increment `review_round` on each aggregator cycle in multi-agent deep-research pipeline so reviewer displays correct round numbers (1, 2, 3) instead of always showing round 1

## [0.1.8] - 2026-05-09

### Added
- Add `include_persona` parameter to `get_system_prompt()` and `get_system_prompt_with_ticker_info()` to allow custom agent identities
- Update system prompt identity to reference AiPriceAction.com branding
- Update data policy to instruct agents to fetch data via tools instead of asking users to paste it

### Fixed
- Allow AI to use web search for news/events instead of redirecting user to paste data

## [0.1.7] - 2026-05-05

### Fixed
- Remove double timezone conversion in AIContextBuilder and add interval arg to single_ticker.py
- Replace English loanword "insight" with Vietnamese "nhận định" in VN prompts
- Replace no-op writer with data integrity reviewer in multi-agent pipeline

## [0.1.6] - 2026-04-28

### Added
- Add risk management priority and balanced analysis directive to system prompts

## [0.1.5] - 2026-04-22

### Added
- Parallelize multi-ticker S3 fetching with ThreadPoolExecutor (concurrency 8)
- Require 3-5 sectors in supervisor with mandatory Banking, Securities, Real Estate
- Add current time and trading hours notice to system prompts

## [0.1.4] - 2026-04-15

### Added
- Add PersistentCheckpointSaver for disk-persisted LangGraph checkpoints
- Add resume support and run script for multi_agent example
- Add source param to build(), fetch_live_data public, MA in live fetch

### Fixed
- Add HTML comment before first `---` to prevent GitHub YAML frontmatter parsing error

## [0.1.3] - 2026-04-08

### Added
- Add multi-agent parallel sector research example and composable system prompts
- Add LangChain agent example with AIContextBuilder and tool-calling research workflow

### Fixed
- Deduplicate 1h bars when use_live=True by normalizing T separator in time strings

## [0.1.2] - 2026-04-01

### Added
- Add live data overlay with `use_live` flag
- Add `utc_offset` parameter for configurable timezone display

## [0.1.1] - 2026-03-25

### Changed
- Add OPENAI_API_KEY validation, remove unused dependencies

## [0.1.0] - 2026-03-18

### Added
- Python SDK with /tickers-style API, disk caching, and 28 tests
- MA/EMA indicator calculation in `get_ohlcv`
- AIContextBuilder with `build()` method for multi-ticker context assembly
- `answer()` method for LLM-powered analysis
- VNINDEX auto-include, `history` param, multi-timeframe examples
- PyPI packaging with author, license, and description
- Greedy backwards fetch and yearly file preference for S3 archive
- Smoke test script for URL validation
- TTL-based cache invalidation for disk cache
