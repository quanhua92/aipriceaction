# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.11] - 2026-05-09

### Changed
- Convert `cmd_analyze` CLI command to async using `asyncio.to_thread` for `builder.build()` and `builder.answer()`
- Unify single-ticker question resolution in CLI with shared `resolve_tui_question()` from `analyze.py`
- Include system prompt in `--context-only` output by default (add `--no-system-prompt` flag to opt out)
- Add short persona header to `get-ohlcv-data` output using `get_system_prompt` from SDK (add `--no-system-prompt` flag to opt out)

### Fixed
- Fix 15 pre-existing test failures: tab switching key consumption, deep research test expectations, settings hint logic, and workflow analyze agent initialization

## [0.1.10] - 2026-05-09

### Added
- Add `/new` slash command to start a fresh chat session (clears history and agent state)
- Add `aipa resume` CLI command to list sessions or open TUI with a resumed session
- Add `/save`, `/resume`, `/sessions`, `/new` to the `?` keyboard shortcuts notification

### Fixed
- Prevent 1-6 tab switch keys from firing while typing in chat input (press Esc first to blur input)
- Fix session resume crash when `app.agent` is not yet initialized during mount

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
