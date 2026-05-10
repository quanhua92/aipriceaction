"""CLI subcommand implementations — thin wrappers around SDK methods."""

from __future__ import annotations

import asyncio
import sys
import time


def _resolve_lang(arg_lang: str | None) -> str:
    """Resolve effective language: CLI arg > saved settings > default."""
    if arg_lang:
        return arg_lang
    from .user_settings import load_settings
    return load_settings().get("language", "en")


async def cmd_analyze(args) -> None:
    from aipriceaction import AIContextBuilder

    lang = _resolve_lang(args.lang)
    builder = AIContextBuilder(lang=lang, ma_type=args.ma_type)

    # --questions: list question bank and exit
    if args.questions:
        mode = "single" if len(args.tickers) == 1 else "multi"
        templates = builder.questions(mode)
        print(f"Available question templates ({mode}, lang={lang}):\n")
        for i, t in enumerate(templates):
            print(f"  [{i}] {t['title']}")
            print(f"      {t['snippet']}\n")
        return

    # Build context
    build_kwargs = dict(
        interval=args.interval,
        limit=args.limit if args.limit is not None else 20,
        source=args.source,
        start_date=args.start_date,
        end_date=args.end_date,
        reference_ticker=args.reference_ticker,
    )

    t0 = time.time()

    include_system_prompt = not args.no_system_prompt

    if len(args.tickers) == 1:
        context = await asyncio.to_thread(
            builder.build,
            ticker=args.tickers[0],
            **build_kwargs,
            include_system_prompt=include_system_prompt,
        )
    else:
        context = await asyncio.to_thread(
            builder.build,
            tickers=args.tickers,
            **build_kwargs,
            include_system_prompt=include_system_prompt,
        )

    build_elapsed = time.time() - t0

    # --context-only: dump raw context and exit
    if args.context_only:
        print(context)
        return

    # LLM analysis
    question = _resolve_cli_question(builder, args)
    if not question:
        print("No question resolved. Use --question TEXT or pick from --questions.", file=sys.stderr)
        sys.exit(1)

    print(f"[build] Context ready ({len(context):,} chars, {build_elapsed:.1f}s)", file=sys.stderr)
    print(f"[analyze] Asking:\n{question}", file=sys.stderr)

    # Agent-based analysis with tool support
    from aipriceaction.settings import settings as sdk_settings
    if not sdk_settings.openai_api_key:
        print("[error] OPENAI_API_KEY not set. Cannot run agent analysis.", file=sys.stderr)
        sys.exit(1)

    from .agents import AgentSession, AgentConfig
    from .agents.callbacks import StreamEventType

    agent_config = AgentConfig(lang=lang)
    session = AgentSession(agent_config)

    message = (
        f"<analysis_context>\n{context}\n</analysis_context>\n\n"
        f"{question}\n\n"
        f"You have tools available (get_live_data, get_ohlcv_data, get_ticker_list). "
        f"Use them if you need additional data beyond what is provided above."
    )

    tokens: list[str] = []
    current_tool: str | None = None
    thinking_buf = ""
    in_thinking = False
    async for event in session.stream(message):
        if event.type == StreamEventType.TOOL_CALL_START:
            if in_thinking:
                print(file=sys.stderr, flush=True)
                in_thinking = False
            print(f"[tool] {event.content}", file=sys.stderr)
            current_tool = event.content
        elif event.type == StreamEventType.TOOL_RESULT:
            if current_tool:
                print(f"[tool-result] {event.content}", file=sys.stderr)
                current_tool = None
        elif event.type == StreamEventType.THINKING:
            thinking_buf += event.content
            if len(thinking_buf) % 80 < len(event.content):
                preview = thinking_buf[-100:].lstrip()
                print(f"\r[thinking] {preview}", end="", file=sys.stderr, flush=True)
            in_thinking = True
        elif event.type == StreamEventType.ERROR:
            if in_thinking:
                print(file=sys.stderr, flush=True)
                in_thinking = False
            print(f"[error] {event.content}", file=sys.stderr)
        elif event.type == StreamEventType.TOKEN:
            if in_thinking:
                print(file=sys.stderr, flush=True)
                in_thinking = False
            tokens.append(event.content)
        elif event.type == StreamEventType.DONE:
            if in_thinking:
                print(file=sys.stderr, flush=True)
                in_thinking = False

    elapsed = time.time() - t0
    print(f"[done] Total: {elapsed:.1f}s", file=sys.stderr)

    response = "".join(tokens).strip()
    if not response:
        print("[error] Agent returned empty response.", file=sys.stderr)
        sys.exit(1)

    print()
    print(response)


def cmd_get_ohlcv(args) -> None:
    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    df = client.get_ohlcv(
        ticker=args.ticker,
        interval=args.interval,
        limit=args.limit,
        start_date=args.start_date,
        end_date=args.end_date,
        source=args.source,
        ma=args.ma,
        ema=args.ema,
    )
    if not getattr(args, "no_system_prompt", False):
        from aipriceaction.system import get_system_prompt

        lang = _resolve_lang(None)
        prompt = get_system_prompt(
            lang,
            include_data_policy=False,
            include_analysis_framework=False,
            include_ma_score=False,
            include_disclaimer=False,
        )
        # Short persona: identity sentence + key communication rules
        paragraphs = prompt.split("\n\n")
        identity_para = paragraphs[1] if len(paragraphs) > 1 else ""
        identity = identity_para.split(".")[0] + "." if "." in identity_para else identity_para
        comm_lines = paragraphs[-1].strip().split("\n")
        print(identity)
        for line in comm_lines[:3]:
            print(line)
        print()
    print(df.to_string(index=False))


def cmd_resume(args) -> None:
    """List recent sessions, or open TUI with a resumed session."""
    from .session import SessionManager

    sm = SessionManager()
    sessions = sm.list_sessions()

    if args.session is None:
        # No arg: list recent sessions and exit
        if not sessions:
            print("No saved sessions found.")
            return
        for i, meta in enumerate(sessions[:20]):
            print(f"  {i:<4} {meta.title:<40} {meta.updated_at:<20} {meta.message_count:>4} msgs  {meta.session_id}")
        print(f"\n{len(sessions)} session(s) total. Use `aipa resume <index>` or `aipa resume <session_id_prefix>` to open in TUI.")
        return

    # Resolve session and open TUI
    target = args.session
    if target.isdigit():
        idx = int(target)
        if 0 <= idx < len(sessions):
            session_id = sessions[idx].session_id
        else:
            print(f"Error: Index {idx} out of range (0-{len(sessions) - 1})", file=sys.stderr)
            sys.exit(1)
    else:
        matches = [s for s in sessions if s.session_id.startswith(target)]
        if len(matches) == 1:
            session_id = matches[0].session_id
        elif len(matches) > 1:
            print(f"Error: {len(matches)} sessions match prefix '{target}'. Use a longer prefix.", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Error: No session found matching '{target}'", file=sys.stderr)
            sys.exit(1)

    from .app import main
    main(resume_session=session_id)


def cmd_deep_research(
    question: str = "",
    resume: str | None = None,
    output: str | None = None,
    lang: str | None = None,
) -> None:
    from .deep_research import run_deep_research

    effective_lang = _resolve_lang(lang)
    asyncio.run(run_deep_research(
        question=question, resume_id=resume, output_file=output, lang=effective_lang,
    ))


def _resolve_cli_question(builder, args) -> str:
    """Resolve the analysis question from args or question bank default.

    For single ticker, delegates to the shared resolve_tui_question().
    Multi-ticker falls back to its own logic (TUI doesn't support multi-ticker).
    """
    if args.question:
        return args.question

    if len(args.tickers) == 1:
        from .analyze import resolve_tui_question
        return resolve_tui_question(builder, args.tickers[0], question_index=0, custom_question=None)

    # Multi-ticker: use question bank template 0
    templates = builder.questions("multi")
    if templates:
        return templates[0]["question"]

    return ""
