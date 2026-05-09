# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
