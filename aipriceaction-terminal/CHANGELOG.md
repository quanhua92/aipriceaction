# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.34] - 2026-05-19

### Added
- `--verbose` flag on `analyze`, `get-ohlcv-data`, `volume-profile`, `live-data`, `performers`, `deep-research` commands for performance debugging
- Timestamped `[VERBOSE HH:MM:SS.mmm +X.XXXs]` logging to stderr at key timing boundaries

## [0.1.33] - 2026-05-18

### Added
- `aipa watchlist` CLI command with `ls`, `get`, `set` subcommands for managing predefined and custom watchlists
- Predefined watchlists: VN30 (BSR replaces DGC), VINGROUP, TM, MASAN, INDEX, CROSS
- Custom watchlists persisted to `~/.aipriceaction/watchlist.json`
- Predefined watchlist reference table added to all 3 skills (aipa-analyze, aipa-data, aipa-research)

## [0.1.32] - 2026-05-16

### Changed
- Require `aipriceaction>=0.1.16` for persistent storage in `~/.aipriceaction/` instead of tempfile
- Align reviewer/aggregator round numbers, increase max rounds to 5, validate supervisor tickers
- Persist CLI and TUI analyze input/output to `~/.aipriceaction/analyze/<uuid>/`

## [0.1.31] - 2026-05-15

### Changed
- Default language changed from English (en) to Vietnamese (vn) in user settings

## [0.1.30] - 2026-05-13

### Changed
- Require `aipriceaction>=0.1.15` for mixed time format fix in `get_ohlcv(start_date=..., ma=True)`

## [0.1.29] - 2026-05-12

### Changed
- Require `aipriceaction>=0.1.14` for live data fallback when S3 has no data

## [0.1.28] - 2026-05-11

### Added
- `--group` sector filter for `aipa performers` and `get_performers` agent tool (e.g. `--group NGAN_HANG`)

## [0.1.27] - 2026-05-11

### Added
- `aipa performers` CLI command: rank top/worst performers by price change, volume, value (close × volume), MA scores, or money flow (defaults to VN stocks)
- `aipa volume-profile TICKER` CLI command: volume-by-price histogram analysis with POC, value area, and statistics
- `get_performers` and `get_volume_profile` agent tools for AI-powered market analysis

### Changed
- Require `aipriceaction>=0.1.13` for performers and volume_profile modules

## [0.1.26] - 2026-05-11

### Added
- Support aggregated intervals (`5m`, `15m`, `30m`, `4h`, `1W`, `2W`) in `get-ohlcv-data`, `live-data`, and agent tools
- Smart MA strategy: `live-data` and agent tools request MA scores only for native intervals (`1D`, `1h`, `1m`), skip for aggregated intervals where MA is meaningless with few bars
- Expand interval selectors in TUI Settings and Workflows tabs to include all 9 intervals
- Move Workflows tab to position 2 (key `2`) right after Chat

### Changed
- Require `aipriceaction>=0.1.12` for client-side OHLCV aggregation and `fetch_live_data` `ma` parameter

### Fixed
- Update agent tool docstrings to list all supported intervals so the AI knows `4h`, `1W`, etc. are valid
- Fix test assertion for auto-detected `reference_ticker` in `/analyze` build call

## [0.1.25] - 2026-05-11

### Changed
- Auto-detect reference ticker by data source: `VNINDEX` for VN stocks, `BTCUSDT` for crypto, `^GSPC` for global/yahoo stocks. `--reference-ticker` flag now defaults to auto-detect instead of hardcoded `VNINDEX`.

## [0.1.24] - 2026-05-11

### Changed
- Require `aipriceaction>=0.1.11` for accurate source-filtered live data snapshots

## [0.1.23] - 2026-05-11

### Added
- `--source` flag to `aipa deep-research` (vn, crypto, global, sjc) — supervisor, workers, tools, prompts, and default questions all adapt to the selected source
- Per-source mandatory sectors: VN (Banking, Securities, Real Estate), crypto (Layer 1, DeFi, AI tokens), global (Technology, Financials, Energy), SJC (Gold / Precious Metals)
- Source-specific default research questions for all four data sources in both EN and VN

### Changed
- `deep-research` pipeline steps description now says "tickers from selected source" instead of "all VN tickers"
- Worker tools `get_ohlcv_data` and `get_ticker_list` now pass/filter by the active source

## [0.1.22] - 2026-05-10

### Changed
- `aipa deep-research` now defaults to context-only (market snapshot). Add `--run` to execute the full multi-agent pipeline (5-10 min)
- Removed `--context-only` flag from `deep-research`; replaced with `--run`
- Pipeline steps summary and `--run` hint printed after context-only snapshot and after full pipeline completion

### Fixed
- `end_node` in deep-research pipeline now falls back to last aggregator output when reviewer rejects all 3 rounds (previously threw KeyError on `final_report`)

## [0.1.21] - 2026-05-10

### Added
- `aipa ticker-list` command to list available tickers with name, group, exchange, and source metadata
- `--source` filter to `aipa live-data` (vn, crypto, global/yahoo, sjc)
- `SJC` -> `SJC-GOLD` ticker alias across analyze, get-ohlcv-data, and live-data

### Fixed
- `--source global` now correctly maps to `yahoo` internally, preventing hangs when fetching global/Yahoo tickers
EOF


### Added
- `aipa live-data` command to fetch the latest candle for all tickers, sorted by trading value (close × volume) descending
- Supports `--top N` to limit results, `--interval` for timeframe, and specific ticker arguments to filter

## [0.1.19] - 2026-05-10

### Changed
- `aipa get-ohlcv-data` accepts multiple space-separated tickers (e.g. `aipa get-ohlcv-data VCB TCB MBB`), fetched in a single API call via the SDK's `tickers=` parameter

## [0.1.18] - 2026-05-10

### Added
- `--context-only` flag to `aipa deep-research` dumps market snapshot without running the pipeline (no API key needed)

### Changed
- `aipa analyze` with no API key prints built context to stdout with setup hint instead of erroring and exiting
- `aipa deep-research` with no API key prints dry-run pipeline outline with setup hint instead of erroring and exiting
- Updated `aipa-analyze` and `aipa-research` skills with agent fallback instructions for when no API key is available

## [0.1.17] - 2026-05-10

### Fixed
- `aipa get-ohlcv-data` now returns data in chronological order (oldest first) instead of reverse year order, so the last rows show the most recent dates (via SDK 0.1.10)

## [0.1.16] - 2026-05-10

### Added
- Ctrl+C to cancel agent streaming in ChatTab (works during chat, /analyze, /deep-research)
- `--verbose` flag to `aipa analyze` to show thinking tokens (hidden by default)
- `[result]` marker line before final response in CLI analyze output

### Changed
- CLI `aipa analyze` and `aipa get-ohlcv-data` auto-uppercase ticker symbols (lowercase input like `vic` now works)
- Thinking preview line in CLI is properly cleared when thinking phase ends

## [0.1.15] - 2026-05-10

### Changed
- Analyze agent (TUI and CLI) can now call tools alongside pre-built context instead of being restricted to provided data only
- CLI `aipa analyze` rewritten to use `AgentSession` with tool support, replacing the plain `builder.answer()` LLM call (tool calls streamed to stderr)

### Added
- `get_live_data` agent tool for quick market overview (top 50 by trading value)
- `get_ohlcv_data` now supports comma-separated multi-ticker input
- System prompts updated to guide AI to call `get_live_data` first for broad overview

## [0.1.14] - 2026-05-10

### Fixed
- Prevent RichLog text corruption when switching tabs during AI streaming (text wrapped to 1 char per line)

## [0.1.13] - 2026-05-10

### Fixed
- Show all non-VN, non-Crypto tickers in Global tab (previously limited to Yahoo source only, excluding SJC gold)

## [0.1.12] - 2026-05-10

### Added
- Ticker data tab with grouped live data tree and detail panel
- Ticker filter input with auto-select first match
- Candlestick chart using plotext with auto-selected candle count based on widget width

### Fixed
- Limit /resume session list to 50 most recent sessions

## [0.1.11] - 2026-05-09

### Changed
- Convert `cmd_analyze` CLI command to async using `asyncio.to_thread` for `builder.build()` and `builder.answer()`
- Unify single-ticker question resolution in CLI with shared `resolve_tui_question()` from `analyze.py`
- Deduplicate TUI analyze logic into shared `analyze.py` module
- Convert `deep_research` to async with output callback for TUI integration
- Include system prompt in `--context-only` output by default (add `--no-system-prompt` flag to opt out)
- Add short persona header to `get-ohlcv-data` output using `get_system_prompt` from SDK (add `--no-system-prompt` flag to opt out)

### Fixed
- Fix 15 pre-existing test failures: tab switching key consumption, deep research test expectations, settings hint logic, and workflow analyze agent initialization
- Retry supervisor when LLM responds with text instead of calling create_subtasks tool

## [0.1.10] - 2026-05-09

### Added
- Add `/new` slash command to start a fresh chat session (clears history and agent state)
- Add `aipa resume` CLI command to list sessions or open TUI with a resumed session
- Add `/save`, `/resume`, `/sessions`, `/new` to the `?` keyboard shortcuts notification

### Fixed
- Prevent 1-6 tab switch keys from firing while typing in chat input (press Esc first to blur input)
- Fix session resume crash when `app.agent` is not yet initialized during mount
- Update help notify with session commands and ctrl+o shortcut

### Docs
- Fix README tab count and add resume examples to Quick Start

## [0.1.9] - 2026-05-09

### Added
- Add persistent chat sessions stored as JSONL files under `~/.aipriceaction/sessions/<uuid>/`
- Add `/save [path]` command to export current chat session to markdown
- Add `/resume [index|session_id]` command to list and reload previous sessions
- Add `/sessions` command as alias for `/resume`
- Auto-create new session on app launch, auto-title from first user message
- Restore LLM context on session resume by prepending conversation history as `<chat_history>` block on first message
- Thinking tokens are excluded from session persistence (only user, assistant, tool_call, tool_result, error, system messages stored)

### Changed
- `/clear` now starts a fresh session (previous session preserved on disk)

## [0.1.8] - 2026-05-09

### Fixed
- Respect `.env` file priority over `settings.json` by parsing `.env` first in `apply_settings_to_env()`, giving correct order: real env vars > `.env` > `settings.json`
- Fix `_value_from_env_or_dotenv()` inverted logic and check `.env` directly when both sources match, so settings tab hints show the correct source
- Guard AnalyzePane with `_ensure_agent()` to prevent `'NoneType' has no attribute 'stream'` crash when no API key is configured at TUI launch

## [0.1.7] - 2026-05-09

### Fixed
- Bridge settings.json API key to SDK Pydantic settings by seeding env vars (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`) at CLI entry point before any SDK import

## [0.1.6] - 2026-05-09

### Added
- Add `aipa setup` interactive CLI command for first-run configuration (language, reference ticker, API key, base URL, model)
- Add `setup_done` flag to user settings, auto-run setup before commands that need an API key (`analyze`, `deep-research`, TUI)
- Add "First-Run Setup" section to README documenting which commands require setup

### Changed
- Lazy-load `AgentSession` in TUI instead of creating on mount, preventing crash when no API key is configured
- Guard agent usage in chat (`_run_agent_chat`, `_run_analyze`, `/clear`) and settings tab with `_ensure_agent()`

### Fixed
- Fix crash on `aipa` launch when no API key is configured (`OpenAIError: Missing credentials`)

## [0.1.5] - 2026-05-09

### Changed
- Expand README with Quick Start examples, full CLI reference for all commands, TUI slash commands, and configuration documentation

## [0.1.4] - 2026-05-09

### Added
- Add unified LLM-powered analyze flow: `aipa analyze` now invokes LLM with question bank by default instead of dumping raw context
- Add `--question TEXT`, `--questions`, and `--context-only` flags to `aipa analyze` CLI command
- Add question bank `Select` dropdown and custom question `Input` to AnalyzePane in Workflows tab
- Add `deep_research.py` module adapting multi-agent pipeline (supervisor -> parallel workers -> aggregator -> reviewer) from examples into a proper importable module
- Add `--resume`, `--output`, and `--lang` flags to `aipa deep-research` command
- Extract `stream_agent_to_log()` shared helper from chat into utils.py, used by both ChatTab and AnalyzePane
- Extend `/analyze` slash command to support template index (`/analyze VCB 2`) and custom questions (`/analyze VCB --question ...`)

### Changed
- Resolve effective language from saved `settings.json` when `--lang` is not explicitly passed on CLI
- Default analyze limit changed from 60 to 20 bars to reduce context size for smaller models
- Require `aipriceaction>=0.1.9`

### Fixed
- Add retry logic (up to 3 attempts) for transient LLM failures in `cmd_analyze`
- Handle string/JSON args in deep-research supervisor tool calls for LLMs that don't parse tool args properly
- Increment `review_round` on each aggregator cycle so reviewer displays correct round numbers

## [0.1.3] - 2026-05-09

### Fixed
- Route CLI entry point through `cli:run` so `--help`, `analyze`, `get-ohlcv-data`, and `deep-research` subcommands work without launching the TUI

## [0.1.2] - 2026-05-09

### Changed
- Require `aipriceaction>=0.1.8` for `include_persona` parameter support

## [0.1.1] - 2026-05-09

### Added
- Add `aipa-cli` entry point alongside `aipa` for `uvx` compatibility

## [0.1.0] - 2026-05-09

### Added
- Add `aipriceaction-terminal` Textual TUI with chat, workflows, and ticker tabs
- Add TickerSelect autocomplete widget using textual-autocomplete
- Add `/exit` command, `/analyze` optional interval arg, and auto-focus chat input
- Add arrow up/down history navigation in chat input
- Add ChatInput widget with slash command autocomplete and history
- Add `/export` command to save AIContextBuilder output to markdown
- Add agents module for AI-powered chat with streaming and tab-switch fix
- Add thinking token detection with collapsible summary in chat
- Add OpenRouterChatOpenAI for reasoning token passthrough and stream thinking visibly
- Add collapsible thinking display with modal viewer in Chat tab
- Add CLI subcommands (`analyze`, `get-ohlcv-data`, `deep-research`) to `aipa` binary
- Add 42 pytest tests for `aipriceaction-terminal`
- Add integration tests with real LangChain message types
- Persist user settings to `~/.aipriceaction/settings.json`
- Show all thinking history with timestamps in ThinkingModal

### Changed
- Reduce default ohlcv limit from 30 to 5 bars
- Replace mock-based tests with real LangChain message types and add integration tests
- Update data policy to fetch via tools
- Extract key bindings and action handlers from `app.py` into separate modules

### Fixed
- Improve TickerSelect autocomplete dropdown size and ticker symbol ranking
- Select autocomplete on Enter instead of submitting when dropdown is open
- Compact ticker list output and buffer streaming tool calls
- Escape key closes thinking modal
- Fix ohlcv docstring default
