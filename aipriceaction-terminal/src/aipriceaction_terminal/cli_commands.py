"""CLI subcommand implementations — thin wrappers around SDK methods."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time

from .verbose import verbose_log


def _resolve_lang(arg_lang: str | None) -> str:
    """Resolve effective language: CLI arg > saved settings > default."""
    if arg_lang:
        return arg_lang
    from .user_settings import load_settings
    return load_settings().get("language", "en")


async def cmd_analyze(args) -> None:
    from aipriceaction import AIContextBuilder

    args.tickers = _resolve_tickers(args.tickers)
    if args.reference_ticker:
        args.reference_ticker = args.reference_ticker.upper()

    lang = _resolve_lang(args.lang)
    verbose_log(f"analyze: resolved lang={lang}")

    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s", stream=sys.stderr, force=True)

    builder = AIContextBuilder(lang=lang, ma_type=args.ma_type)

    # Auto-detect reference ticker if user didn't explicitly override
    if args.reference_ticker is None and len(args.tickers) == 1:
        from .analyze import _resolve_reference_ticker
        args.reference_ticker = _resolve_reference_ticker(builder, args.tickers[0])

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
        source=_resolve_source(args.source),
        start_date=args.start_date,
        end_date=args.end_date,
        reference_ticker=args.reference_ticker,
    )

    t0 = time.time()

    verbose_log(f"analyze: starting context build tickers={args.tickers}")
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
    verbose_log(f"analyze: context built ({len(context):,} chars, {build_elapsed:.3f}s)")

    # --context-only: dump raw context and exit
    if args.context_only:
        print(context)
        return

    # LLM analysis
    question = _resolve_cli_question(builder, args)
    verbose_log(f"analyze: resolved question ({len(question)} chars)")
    if not question:
        print("No question resolved. Use --question TEXT or pick from --questions.", file=sys.stderr)
        sys.exit(1)

    print(f"[build] Context ready ({len(context):,} chars, {build_elapsed:.1f}s)", file=sys.stderr)
    print(f"[analyze] Asking:\n{question}", file=sys.stderr)

    # Agent-based analysis with tool support
    from aipriceaction.settings import settings as sdk_settings
    if not sdk_settings.openai_api_key:
        print(context)
        print("[info] OPENAI_API_KEY not set. Context printed above (no LLM analysis).", file=sys.stderr)
        print("[info] Run 'aipa setup' to configure your API key.", file=sys.stderr)
        return

    from .agents import AgentSession, AgentConfig
    from .agents.callbacks import StreamEventType

    agent_config = AgentConfig(lang=lang)
    session = AgentSession(agent_config)
    verbose_log("analyze: agent session created, starting LLM stream")

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
    verbose = getattr(args, "verbose", False)

    def _flush_thinking() -> None:
        """Clear the updating preview line and print remaining thinking content."""
        nonlocal in_thinking
        if not in_thinking:
            return
        # Clear the truncated \r preview line
        print(f"\r{' ' * 120}\r", end="", file=sys.stderr, flush=True)
        in_thinking = False

    async for event in session.stream(message):
        if event.type == StreamEventType.TOOL_CALL_START:
            _flush_thinking()
            print(f"[tool] {event.content}", file=sys.stderr)
            current_tool = event.content
        elif event.type == StreamEventType.TOOL_RESULT:
            if current_tool:
                print(f"[tool-result] {event.content}", file=sys.stderr)
                current_tool = None
        elif event.type == StreamEventType.THINKING:
            thinking_buf += event.content
            if verbose and len(thinking_buf) % 80 < len(event.content):
                preview = thinking_buf[-100:].lstrip()
                print(f"\r[thinking] {preview}", end="", file=sys.stderr, flush=True)
            in_thinking = True
        elif event.type == StreamEventType.ERROR:
            _flush_thinking()
            print(f"[error] {event.content}", file=sys.stderr)
        elif event.type == StreamEventType.TOKEN:
            _flush_thinking()
            tokens.append(event.content)
        elif event.type == StreamEventType.DONE:
            _flush_thinking()

    elapsed = time.time() - t0
    verbose_log(f"analyze: LLM stream complete ({elapsed:.3f}s)")
    print(f"[done] Total: {elapsed:.1f}s", file=sys.stderr)

    response = "".join(tokens).strip()
    if not response:
        print("[error] Agent returned empty response.", file=sys.stderr)
        sys.exit(1)

    print("\n[result]", file=sys.stderr)
    print(response)

    # Persist input + output to ~/.aipriceaction/analyze/<uuid>/
    try:
        import uuid
        from pathlib import Path
        from datetime import datetime, timezone

        session_id = str(uuid.uuid4())
        session_dir = Path.home() / ".aipriceaction" / "analyze" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "session_id": session_id,
            "tickers": args.tickers,
            "interval": args.interval,
            "reference_ticker": args.reference_ticker,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        (session_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        (session_dir / "input.md").write_text(message)
        (session_dir / "output.md").write_text(response)
    except Exception:
        pass


def cmd_get_ohlcv(args) -> None:
    from aipriceaction import AIPriceAction

    raw_tickers = _resolve_tickers(args.tickers)
    client = AIPriceAction()

    verbose_log(f"get-ohlcv: fetching tickers={raw_tickers}")

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

    if len(raw_tickers) == 1:
        df = client.get_ohlcv(
            ticker=raw_tickers[0],
            interval=args.interval,
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
            source=_resolve_source(args.source),
            ma=args.ma,
            ema=args.ema,
        )
        print(df.to_string(index=False))
    else:
        df = client.get_ohlcv(
            tickers=raw_tickers,
            interval=args.interval,
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
            source=_resolve_source(args.source),
            ma=args.ma,
            ema=args.ema,
        )
        print(df.to_string(index=False))

    verbose_log(f"get-ohlcv: fetched {len(df)} rows")


_TICKER_ALIASES = {"SJC": "SJC-GOLD"}

_SOURCE_ALIASES = {"global": "yahoo"}


def _resolve_tickers(raw: list[str]) -> list[str]:
    return [_TICKER_ALIASES.get(t.upper(), t.upper()) for t in raw]


def _resolve_source(source: str | None) -> str | None:
    if source is None:
        return None
    return _SOURCE_ALIASES.get(source, source)


def cmd_live_data(args) -> None:
    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    tickers = _resolve_tickers(args.tickers) if args.tickers else None
    interval = args.interval
    top = args.top
    source = args.source
    verbose_log(f"live-data: interval={interval}, tickers={tickers}, top={top}")

    # Build source filter set from ticker metadata
    source_set: set[str] | None = None
    if source:
        meta = client.get_tickers(source=source)
        source_set = {t.ticker for t in meta}

    try:
        is_native = interval in {"1D", "1h", "1m"}
        verbose_log(f"live-data: fetching live data (native={is_native})")
        data = client.fetch_live_data(interval, ma=is_native)
    except Exception as e:
        print(f"Error fetching live data: {e}", file=sys.stderr)
        sys.exit(1)
    if data is None:
        print("Failed to fetch live data.", file=sys.stderr)
        sys.exit(1)

    verbose_log(f"live-data: fetched {len(data)} symbols")
    rows: list[dict] = []
    for symbol, candles in data.items():
        if tickers and symbol not in tickers:
            continue
        if source_set and symbol not in source_set:
            continue
        if not candles:
            continue
        c = candles[-1]
        close = c.get("close") or 0
        vol = c.get("volume") or 0
        rows.append({
            "ticker": symbol,
            "time": client.convert_time(str(c.get("time", "")), interval),
            "open": c.get("open"),
            "high": c.get("high"),
            "low": c.get("low"),
            "close": close,
            "volume": vol,
            "value": close * vol,
            "close_changed": c.get("close_changed"),
            "volume_changed": c.get("volume_changed"),
            "ma10_score": c.get("ma10_score"),
            "ma50_score": c.get("ma50_score"),
        })

    if not rows:
        print(f"No data found for tickers={tickers or 'all'} ({interval}).", file=sys.stderr)
        sys.exit(1)

    if not tickers:
        rows.sort(key=lambda r: r["value"], reverse=True)
        rows = rows[:top]

    import pandas as pd
    df = pd.DataFrame(rows)
    # Reorder columns: drop 'value' from display, put it after volume
    cols = ["ticker", "time", "open", "high", "low", "close", "volume", "close_changed", "volume_changed", "ma10_score", "ma50_score"]
    df = df[cols]
    print(df.to_string(index=False))


def cmd_ticker_list(args) -> None:
    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    tickers = client.get_tickers(source=_resolve_source(args.source))

    if args.group:
        tickers = [t for t in tickers if (t.group or "") == args.group]

    if not tickers:
        print("No tickers found.", file=sys.stderr)
        sys.exit(1)

    if args.compact:
        print(", ".join(t.ticker for t in tickers))
        return

    import pandas as pd

    df = pd.DataFrame([
        {"ticker": t.ticker, "name": t.name or "", "group": t.group or "", "exchange": t.exchange or "", "source": t.source}
        for t in tickers
    ])
    print(df.to_string(index=False))
    print(f"\nTotal: {len(tickers)} tickers")


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
    run_pipeline: bool = False,
    source: str | None = None,
    verbose: bool = False,
) -> None:
    from .deep_research import run_deep_research

    effective_lang = _resolve_lang(lang)
    asyncio.run(run_deep_research(
        question=question, resume_id=resume, output_file=output, lang=effective_lang,
        run_pipeline=run_pipeline, source=_resolve_source(source),
        verbose=verbose,
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


def cmd_performers(args) -> None:
    """CLI handler: aipa performers."""
    from aipriceaction import AIPriceAction
    from aipriceaction.performers import build_performers

    client = AIPriceAction()
    source = _resolve_source(args.source)
    verbose_log(f"performers: source={source}, sort_by={args.sort_by}")

    # Fetch live 1D data with MA indicators
    verbose_log("performers: fetching live data")
    data = client.fetch_live_data("1D", ma=True)
    if not data:
        print("No live data available.", file=sys.stderr)
        sys.exit(1)

    verbose_log(f"performers: live data fetched, {len(data)} symbols")

    # Build sector map from ticker metadata
    sector_map: dict[str, str] = {}
    if source:
        tickers_meta = client.get_tickers(source=source)
        sector_map = {t.ticker: t.group for t in tickers_meta if t.group}
        # Filter live data to source tickers
        source_symbols = {t.ticker for t in tickers_meta}
        data = {k: v for k, v in data.items() if k in source_symbols}

    # Filter by group/sector if specified
    if args.group:
        group_upper = args.group.upper()
        data = {k: v for k, v in data.items() if sector_map.get(k, "").upper() == group_upper}

    top, worst = build_performers(
        data, sector_map,
        sort_by=args.sort_by,
        direction=args.direction,
        limit=args.limit,
        min_volume=args.min_volume,
        source=source,
    )

    verbose_log(f"performers: ranked {len(top)} top, {len(worst)} worst")

    import pandas as pd

    if top:
        print(f"\n=== Top {len(top)} Performers (by {args.sort_by}, {args.direction}) ===")
        df = pd.DataFrame([_performer_to_dict(p) for p in top])
        print(df.to_string(index=False))

    if worst:
        print(f"\n=== Worst {len(worst)} Performers (by {args.sort_by}, {args.direction}) ===")
        df = pd.DataFrame([_performer_to_dict(p) for p in worst])
        print(df.to_string(index=False))


def _performer_to_dict(p) -> dict:
    """Convert PerformerInfo to a flat dict for display."""
    return {
        "ticker": p.symbol,
        "close": p.close,
        "volume": p.volume,
        "value": f"{p.value:,.0f}",
        "close_changed": f"{p.close_changed:+.2f}" if p.close_changed is not None else "",
        "volume_changed": f"{p.volume_changed:+.2f}" if p.volume_changed is not None else "",
        "ma10_score": f"{p.ma10_score:.1f}" if p.ma10_score is not None else "",
        "ma50_score": f"{p.ma50_score:.1f}" if p.ma50_score is not None else "",
        "ma200_score": f"{p.ma200_score:.1f}" if p.ma200_score is not None else "",
        "sector": p.sector or "",
    }


def cmd_watchlist(args) -> None:
    from .predefined_watchlists import get_predefined_names, get_predefined_tickers, is_predefined, PREDEFINED_DESCRIPTIONS
    from .user_watchlist import get_watchlist, load_watchlists, set_watchlist

    subcmd = getattr(args, "watchlist_command", None)

    if subcmd == "ls":
        for name in get_predefined_names():
            desc = PREDEFINED_DESCRIPTIONS.get(name, "")
            tickers = get_predefined_tickers(name)
            print(f"  {name:<12} [predefined] ({len(tickers)} tickers) {desc}")
        custom = load_watchlists()
        if custom:
            for name, tickers in custom.items():
                print(f"  {name:<12} [custom]     ({len(tickers)} tickers)")
        else:
            print("  (no custom watchlists)")

    elif subcmd == "get":
        name = args.name.upper()
        tickers = get_watchlist(name)
        if tickers is None:
            print(f"Watchlist '{name}' not found.", file=sys.stderr)
            sys.exit(1)
        print(" ".join(tickers))
        print(f"({len(tickers)} tickers)", file=sys.stderr)

    elif subcmd == "set":
        name = args.name.upper()
        tickers = [t.upper() for t in args.tickers]
        try:
            set_watchlist(name, tickers)
            print(f"Watchlist '{name}' saved with {len(tickers)} tickers.", file=sys.stderr)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif subcmd == "rm":
        name = args.name.upper()
        if is_predefined(name):
            print(f"Error: '{name}' is a predefined watchlist and cannot be deleted.", file=sys.stderr)
            sys.exit(1)
        from .user_watchlist import delete_watchlist
        if delete_watchlist(name):
            print(f"Watchlist '{name}' deleted.", file=sys.stderr)
        else:
            print(f"Watchlist '{name}' not found.", file=sys.stderr)
            sys.exit(1)

    else:
        print("Usage: aipa watchlist [ls|get|set|rm]", file=sys.stderr)
        print("  ls          List all watchlists", file=sys.stderr)
        print("  get <name>  Show tickers for a watchlist", file=sys.stderr)
        print("  set <name> <tickers...>  Create/update a custom watchlist", file=sys.stderr)
        print("  rm  <name>  Delete a custom watchlist", file=sys.stderr)
        sys.exit(1)


def cmd_volume_profile(args) -> None:
    """CLI handler: aipa volume-profile."""
    from datetime import date

    from aipriceaction import AIPriceAction
    from aipriceaction.volume_profile import compute_volume_profile

    client = AIPriceAction()
    ticker = args.ticker.upper()
    verbose_log(f"volume-profile: initializing for {ticker}")

    # Resolve date range
    if args.date:
        start_date = end_date = args.date
    elif args.start_date:
        start_date = args.start_date
        end_date = args.end_date or args.start_date
    else:
        today = date.today().isoformat()
        start_date = end_date = today

    # Resolve source for tick size
    source = _resolve_source(args.source) or "vn"
    # Auto-detect from ticker metadata if not specified
    if args.source is None:
        try:
            tickers_meta = client.get_tickers()
            for t in tickers_meta:
                if t.ticker == ticker:
                    source = t.source or "vn"
                    break
        except Exception:
            pass

    # Fetch 1m data
    verbose_log(f"volume-profile: fetching 1m data {start_date} to {end_date}")
    df = client.get_ohlcv(
        ticker,
        interval="1m",
        start_date=start_date,
        end_date=end_date,
        ma=False,
    )

    if df is None or df.empty:
        print(f"No 1m data found for {ticker} on {start_date}.", file=sys.stderr)
        sys.exit(1)

    verbose_log(f"volume-profile: fetched {len(df)} 1m candles")

    result = compute_volume_profile(
        df, ticker,
        source=source,
        bins=args.bins,
        value_area_pct=args.value_area_pct,
    )

    verbose_log(f"volume-profile: computed {result.total_volume:,} total volume, {len(result.profile)} price levels")

    print(f"\n=== Volume Profile: {result.symbol} ({start_date}) ===")
    print(f"Total Volume: {result.total_volume:,}  |  Minutes: {result.total_minutes}")
    print(f"Price Range: {result.price_range.low:.2f} - {result.price_range.high:.2f}  "
          f"(spread: {result.price_range.spread:.2f})")
    print(f"\nPOC: {result.poc.price:.2f}  volume={result.poc.volume:,.0f}  "
          f"({result.poc.percentage:.1f}%)")
    print(f"Value Area: {result.value_area.low:.2f} - {result.value_area.high:.2f}  "
          f"volume={result.value_area.volume:,.0f}  ({result.value_area.percentage:.1f}%)")

    if result.statistics:
        s = result.statistics
        print("\nStatistics:")
        print(f"  Mean: {s.mean_price:.2f}  Median: {s.median_price:.2f}  "
              f"StdDev: {s.std_deviation:.2f}  Skewness: {s.skewness:.4f}")

    if result.profile:
        print(f"\n{'Price':>12}  {'Volume':>12}  {'%':>6}  {'Cum%':>6}  Bar")
        print("-" * 60)
        max_vol = max(p.volume for p in result.profile) if result.profile else 1.0
        for level in result.profile:
            bar_len = int(level.volume / max_vol * 30) if max_vol > 0 else 0
            bar = "\u2588" * bar_len
            print(f"{level.price:>12.2f}  {level.volume:>12,.0f}  {level.percentage:>5.1f}%  "
                  f"{level.cumulative_percentage:>5.1f}%  {bar}")


# ── Fundamentals ──────────────────────────────────────────────────────────────

_PCT_FIELDS: frozenset[str] = frozenset({
    "roe", "roa", "roic", "gross_margin", "after_tax_profit_margin",
    "pre_tax_profit_margin", "ebit_margin", "net_interest_margin",
    "dividend_yield", "npl", "cir", "cost_to_income", "car", "casa_ratio",
    "deposit_growth", "loans_growth", "total_equity_total_asset",
    "equity_to_liabilities", "equity_to_loans",
    "non_and_interest_income", "loans_loss_reserve_to_loans",
    "loans_loss_reserves_to_npl", "provision_to_outstanding_loans",
    "average_cost_of_financing", "average_yield_on_earning_assets",
})

_CATEGORY_FIELDS: dict[str, list[tuple[str, str]]] = {
    "valuation": [
        ("PE", "pe"), ("PB", "pb"), ("PS", "ps"),
        ("EV/EBITDA", "ev_to_ebitda"), ("Price/CashFlow", "price_to_cash_flow"),
        ("Dividend Yield", "dividend_yield"), ("Market Cap", "market_cap"),
    ],
    "profitability": [
        ("ROE", "roe"), ("ROA", "roa"), ("ROIC", "roic"),
        ("Gross Margin", "gross_margin"), ("After-Tax Margin", "after_tax_profit_margin"),
        ("Pre-Tax Margin", "pre_tax_profit_margin"), ("EBIT Margin", "ebit_margin"),
        ("Net Interest Margin", "net_interest_margin"), ("EBIT", "ebit"), ("EBITDA", "ebitda"),
    ],
    "efficiency": [
        ("Asset Turnover", "asset_turnover"), ("Fixed Asset Turnover", "fixed_asset_turnover"),
        ("Cash Cycle", "cash_cycle"), ("DSO", "day_sale_outstanding"),
        ("DIO", "days_inventory_outstanding"), ("DPO", "days_payable_outstanding"),
    ],
    "leverage": [
        ("Debt/Equity", "debt_to_equity"), ("Debt per Equity", "debt_per_equity"),
        ("Financial Leverage", "financial_leverage"),
        ("Equity/Liabilities", "equity_to_liabilities"), ("Equity/Loans", "equity_to_loans"),
        ("Equity/Total Asset", "total_equity_total_asset"), ("Owners Equity", "owners_equity"),
        ("Equity", "equity"),
    ],
    "liquidity": [
        ("Current Ratio", "current_ratio"), ("Quick Ratio", "quick_ratio"), ("Cash Ratio", "cash_ratio"),
    ],
    "bank": [
        ("NPL", "npl"), ("LDR", "ldr_loan_deposit_ratio"), ("CAR", "car"),
        ("CASA Ratio", "casa_ratio"), ("CIR", "cir"), ("Cost/Income", "cost_to_income"),
        ("Non-Interest Income", "non_and_interest_income"),
        ("Deposit Growth", "deposit_growth"), ("Loans Growth", "loans_growth"),
        ("LLR/Loans", "loans_loss_reserve_to_loans"), ("LLR/NPL", "loans_loss_reserves_to_npl"),
        ("Provision/Loans", "provision_to_outstanding_loans"),
        ("Avg Cost of Financing", "average_cost_of_financing"),
        ("Avg Yield Earning Assets", "average_yield_on_earning_assets"),
    ],
}


def _fmt_fund(v: float | int | None, field_name: str) -> str:
    if v is None:
        return "N/A"
    if field_name in _PCT_FIELDS:
        return f"{v * 100:.2f}%"
    if field_name in ("market_cap", "outstanding_shares", "employees", "current_price"):
        return f"{v:,.0f}"
    return f"{v:.2f}"


def _resolve_ticker_source(args) -> list[str]:
    if getattr(args, "watchlist", None):
        from .user_watchlist import get_watchlist
        name = args.watchlist.upper()
        tickers = get_watchlist(name)
        if tickers is None:
            print(f"Watchlist '{name}' not found.", file=sys.stderr)
            sys.exit(1)
        return tickers
    if args.tickers:
        return _resolve_tickers(args.tickers)
    from aipriceaction import AIPriceAction
    client = AIPriceAction()
    return [t.ticker for t in client.get_tickers(source="vn")]


def _fund_info(args) -> None:
    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    ticker = args.ticker.upper()
    verbose_log(f"fundamentals info: {ticker}")

    ci = client.get_company_info(ticker, source=_resolve_source(args.source))
    if ci is None:
        print(f"No company info found for {ticker}.", file=sys.stderr)
        sys.exit(1)

    industry = ci.industry or "N/A"
    print(f"\n=== {ci.symbol} — {industry} ===\n")
    print(f"  Industry:           {industry}")
    print(f"  Market Cap:         {_fmt_fund(ci.market_cap, 'market_cap')} VND")
    print(f"  Current Price:      {_fmt_fund(ci.current_price, 'current_price')} VND")
    print(f"  Outstanding Shares: {_fmt_fund(ci.outstanding_shares, 'outstanding_shares')}")
    print(f"  Employees:          {_fmt_fund(ci.employees, 'employees')}")
    print(f"  Established:        {ci.established_year or 'N/A'}")
    print(f"  Website:            {ci.website or 'N/A'}")

    if ci.shareholders:
        sorted_sh = sorted(ci.shareholders, key=lambda s: s.percentage or 0, reverse=True)
        print(f"\n  Top Shareholders ({len(ci.shareholders)} total):")
        for s in sorted_sh[:15]:
            pct = f"{s.percentage * 100:.2f}%" if s.percentage is not None else "N/A"
            print(f"    {s.name:45s} {pct:>8s}")

    if ci.officers:
        print(f"\n  Officers ({len(ci.officers)} total):")
        for o in ci.officers:
            pct = f" ({o.percentage * 100:.2f}% ownership)" if o.percentage else ""
            print(f"    {o.name:35s}  {o.position}{pct}")


def _fund_ratios(args) -> None:
    import json

    from aipriceaction import AIPriceAction

    client = AIPriceAction()
    ticker = args.ticker.upper()
    verbose_log(f"fundamentals ratios: {ticker}")

    fr = client.get_financial_ratios(ticker, source=_resolve_source(args.source))
    if fr is None:
        print(f"No financial ratios found for {ticker}.", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(fr.to_dict(), indent=2))
        return

    entries = fr.ratios
    if args.year is not None:
        entries = [r for r in entries if r.year_report == args.year]
    elif args.latest:
        entries = entries[:1]
    elif args.yearly:
        yearly = [r for r in entries if r.ratio_type == "RATIO_YEAR"]
        if not yearly:
            yearly = [r for r in entries if r.length_report in (5, 12)]
        if yearly:
            entries = yearly

    if not entries:
        print(f"No matching ratio entries for {ticker}.", file=sys.stderr)
        sys.exit(1)

    categories = [args.category] if args.category else list(_CATEGORY_FIELDS.keys())

    for entry in entries:
        period_type = "yearly" if entry.length_report in (5, 12) else f"Q{entry.length_report}"
        print(f"\n=== {ticker} Financial Ratios (year={entry.year_report}, {period_type}) ===")

        for cat_name in categories:
            fields = _CATEGORY_FIELDS[cat_name]
            has_data = any(getattr(entry, attr, None) is not None for _, attr in fields)
            if not has_data:
                continue
            print(f"\n  {cat_name.title()}:")
            for label, attr in fields:
                val = getattr(entry, attr, None)
                print(f"    {label:25s} {_fmt_fund(val, attr)}")

    print(f"\n  Total entries: {fr.count}  |  Showing: {len(entries)}")


def _fund_rank(args) -> None:
    import pandas as pd

    from aipriceaction import AIPriceAction, build_fundamental_ranking

    client = AIPriceAction()
    tickers = _resolve_ticker_source(args)
    verbose_log(f"fundamentals rank: {len(tickers)} tickers, sort_by={args.sort_by}")

    if not tickers:
        print("No tickers to rank.", file=sys.stderr)
        sys.exit(1)

    entries = build_fundamental_ranking(
        client, tickers,
        sort_by=args.sort_by,
        direction=args.direction,
        limit=args.limit,
        source=_resolve_source(args.source),
        yearly_only=not args.latest,
    )

    if not entries:
        print("No results.", file=sys.stderr)
        return

    sort_field = args.sort_by
    rows = []
    for e in entries:
        r = e.latest_ratio
        val = e.rank_value
        if val is not None and sort_field in _PCT_FIELDS:
            val_str = f"{val * 100:.2f}%"
        elif val is not None:
            val_str = f"{val:,.2f}"
        else:
            val_str = "N/A"
        rows.append({
            "#": e.rank,
            "ticker": e.ticker,
            sort_field: val_str,
            "pe": f"{r.pe:.1f}" if r and r.pe is not None else "N/A",
            "pb": f"{r.pb:.2f}" if r and r.pb is not None else "N/A",
            "roe": f"{r.roe * 100:.1f}%" if r and r.roe is not None else "N/A",
            "industry": (e.industry or "")[:25],
        })

    df = pd.DataFrame(rows)
    print(f"\n=== Top {len(entries)} by {sort_field} ({args.direction}) ===")
    print(df.to_string(index=False))


def _fund_screen(args) -> None:
    import pandas as pd

    from aipriceaction import AIPriceAction, screen_fundamentals

    client = AIPriceAction()
    tickers = _resolve_ticker_source(args)
    verbose_log(f"fundamentals screen: {len(tickers)} tickers")

    if not tickers:
        print("No tickers to screen.", file=sys.stderr)
        sys.exit(1)

    entries = screen_fundamentals(
        client, tickers,
        sort_by=args.sort_by,
        direction=args.direction,
        limit=args.limit,
        source=_resolve_source(args.source),
        yearly_only=not args.latest,
        pe_min=args.pe_min, pe_max=args.pe_max,
        pb_min=args.pb_min, pb_max=args.pb_max,
        roe_min=args.roe_min, roe_max=args.roe_max,
        roa_min=args.roa_min, roa_max=args.roa_max,
        dividend_yield_min=args.dividend_yield_min, dividend_yield_max=args.dividend_yield_max,
        debt_to_equity_max=args.debt_to_equity_max,
        npl_max=args.npl_max,
        car_min=args.car_min,
        cir_max=args.cir_max,
        market_cap_min=args.market_cap_min, market_cap_max=args.market_cap_max,
        industry=args.industry,
    )

    if not entries:
        print("No tickers match the screening criteria.", file=sys.stderr)
        return

    sort_field = args.sort_by
    rows = []
    for e in entries:
        r = e.latest_ratio
        val = e.rank_value
        if val is not None and sort_field in _PCT_FIELDS:
            val_str = f"{val * 100:.2f}%"
        elif val is not None:
            val_str = f"{val:,.2f}"
        else:
            val_str = "N/A"
        rows.append({
            "#": e.rank,
            "ticker": e.ticker,
            sort_field: val_str,
            "pe": f"{r.pe:.1f}" if r and r.pe is not None else "N/A",
            "roe": f"{r.roe * 100:.1f}%" if r and r.roe is not None else "N/A",
            "npl": f"{r.npl * 100:.2f}%" if r and r.npl is not None else "N/A",
            "car": f"{r.car * 100:.2f}%" if r and r.car is not None else "N/A",
            "industry": (e.industry or "")[:25],
        })

    filters = []
    for attr_name in ("pe_min", "pe_max", "pb_min", "pb_max", "roe_min", "roe_max",
                       "roa_min", "roa_max", "npl_max", "car_min", "cir_max",
                       "dividend_yield_min", "dividend_yield_max",
                       "debt_to_equity_max", "market_cap_min", "market_cap_max",
                       "industry"):
        v = getattr(args, attr_name, None)
        if v is not None:
            filters.append(f"{attr_name}={v}")
    filter_desc = ", ".join(filters) if filters else "no filters"

    df = pd.DataFrame(rows)
    print(f"\n=== Screened: {filter_desc} ({len(entries)} match, by {sort_field} {args.direction}) ===")
    print(df.to_string(index=False))


def cmd_fundamentals(args) -> None:
    subcmd = getattr(args, "fundamentals_command", None)
    if subcmd == "info":
        _fund_info(args)
    elif subcmd == "ratios":
        _fund_ratios(args)
    elif subcmd == "rank":
        _fund_rank(args)
    elif subcmd == "screen":
        _fund_screen(args)
    else:
        print("Usage: aipa fundamentals [info|ratios|rank|screen]", file=sys.stderr)
        print("  info TICKER         Company profile, shareholders, officers", file=sys.stderr)
        print("  ratios TICKER       Financial ratios by period", file=sys.stderr)
        print("  rank                Rank tickers by a fundamental field", file=sys.stderr)
        print("  screen              Screen tickers by fundamental criteria", file=sys.stderr)
        sys.exit(1)
