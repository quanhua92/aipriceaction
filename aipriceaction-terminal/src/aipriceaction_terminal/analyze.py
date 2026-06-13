"""Shared analyze logic used by ChatTab, AnalyzePane, and CLI."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from textual.widgets import RichLog

from .utils import stream_agent_to_log

_ANALYZE_DIR = Path.home() / ".aipriceaction" / "analyze"

_DEFAULT_REFERENCE: dict[str, str] = {
    "crypto": "BTCUSDT",
    "vn": "VNINDEX",
    "yahoo": "^GSPC",
}


def _resolve_reference_ticker(builder: object, ticker: str) -> str:
    """Pick the right reference ticker based on the primary ticker's source."""
    try:
        all_tickers = builder._client.get_tickers()
        for t in all_tickers:
            if t.ticker == ticker:
                return _DEFAULT_REFERENCE.get(t.source, "VNINDEX")
    except Exception:
        pass
    # Fallback: USDT suffix → crypto
    if ticker.upper().endswith("USDT"):
        return "BTCUSDT"
    return "VNINDEX"


def resolve_tui_question(
    builder: object,
    ticker: str,
    question_index: int | None,
    custom_question: str | None,
) -> str:
    """Resolve the analysis question for TUI /analyze command."""
    if custom_question:
        return custom_question

    templates = builder.questions("single")
    if not templates:
        return f"Analyze {ticker} based on the provided data."

    idx = question_index if question_index is not None else 0
    idx = max(0, min(idx, len(templates) - 1))
    template = templates[idx]

    try:
        return template["question"].format(ticker=ticker)
    except KeyError:
        return template["question"]


async def run_tui_analyze(
    log: RichLog,
    agent: object,
    builder: object,
    ticker: str,
    interval: str,
    *,
    source: str | None = None,
    cancel_event: asyncio.Event | None = None,
    question_index: int | None = None,
    custom_question: str | None = None,
    prefix: str | None = None,
    reference_ticker: str | None = None,
    on_thinking_update: Callable[[str], None] | None = None,
    on_thinking_done: Callable[[str], None] | None = None,
    on_message: Callable[[dict], None] | None = None,
) -> None:
    """Build context, resolve question, and stream AI analysis to a RichLog.

    Shared by ChatTab and AnalyzePane to avoid duplicating the analyze flow.
    """
    reference_ticker = reference_ticker or _resolve_reference_ticker(builder, ticker)

    context = await asyncio.to_thread(
        builder.build, ticker=ticker, interval=interval,
        include_system_prompt=False,
        source=source,
        reference_ticker=reference_ticker,
    )

    log.write(f"[dim]Context ready: {len(context):,} chars[/dim]")

    question = resolve_tui_question(
        builder, ticker, question_index, custom_question,
    )

    message = (
        f"<analysis_context>\n{context}\n</analysis_context>\n\n"
        f"{question}\n\n"
        f"You have tools available (get_live_data, get_ohlcv_data, get_ticker_list). "
        f"Use them if you need additional data beyond what is provided above."
    )

    if prefix:
        message = prefix + message

    response_text = await stream_agent_to_log(
        log,
        agent,
        message,
        cancel_event=cancel_event,
        on_thinking_update=on_thinking_update,
        on_thinking_done=on_thinking_done,
        on_message=on_message,
    )

    # Persist input + output to ~/.aipriceaction/analyze/<uuid>/
    try:
        session_id = str(uuid.uuid4())
        session_dir = _ANALYZE_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "session_id": session_id,
            "ticker": ticker,
            "interval": interval,
            "reference_ticker": reference_ticker,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        (session_dir / "input.md").write_text(message)
        (session_dir / "output.md").write_text(response_text)
    except Exception:
        pass  # best-effort, never block the UI
