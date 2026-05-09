# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
