"""CLI subcommand implementations — thin wrappers around SDK methods."""

from __future__ import annotations

import sys
import time


def _resolve_lang(arg_lang: str | None) -> str:
    """Resolve effective language: CLI arg > saved settings > default."""
    if arg_lang:
        return arg_lang
    from .user_settings import load_settings
    return load_settings().get("language", "en")


def cmd_analyze(args) -> None:
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

    if len(args.tickers) == 1:
        context = builder.build(
            ticker=args.tickers[0],
            **build_kwargs,
            include_system_prompt=not args.context_only,
        )
    else:
        context = builder.build(
            tickers=args.tickers,
            **build_kwargs,
            include_system_prompt=not args.context_only,
        )

    build_elapsed = time.time() - t0
    ticker_label = args.tickers[0] if len(args.tickers) == 1 else ",".join(args.tickers)

    # --context-only: dump raw context and exit
    if args.context_only:
        print(context)
        return

    # LLM analysis
    question = _resolve_question(builder, args)
    if not question:
        print("No question resolved. Use --question TEXT or pick from --questions.", file=sys.stderr)
        sys.exit(1)

    print(f"[build] Context ready ({len(context):,} chars, {build_elapsed:.1f}s)", file=sys.stderr)
    print(f"[analyze] Asking: {question[:80]}{'...' if len(question) > 80 else ''}", file=sys.stderr)

    try:
        response = builder.answer(question)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        sys.exit(1)

    elapsed = time.time() - t0
    print(f"[done] Total: {elapsed:.1f}s", file=sys.stderr)
    print()

    if not response.strip():
        print("[warn] LLM returned empty response. The model may not support the context size or language mismatch.", file=sys.stderr)
        sys.exit(1)

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
    print(df.to_string(index=False))


def cmd_deep_research(
    question: str = "",
    resume: str | None = None,
    output: str | None = None,
    lang: str | None = None,
) -> None:
    from .deep_research import run_deep_research

    effective_lang = _resolve_lang(lang)
    run_deep_research(question=question, resume_id=resume, output_file=output, lang=effective_lang)


def _resolve_question(builder, args) -> str:
    """Resolve the analysis question from args or question bank default."""
    if args.question:
        return args.question

    # For single ticker, use question bank template 0 with {ticker} interpolated
    if len(args.tickers) == 1:
        templates = builder.questions("single")
        if templates:
            try:
                return templates[0]["question"].format(ticker=args.tickers[0])
            except KeyError:
                return templates[0]["question"]

    # For multi-ticker, use question bank template 0
    templates = builder.questions("multi")
    if templates:
        return templates[0]["question"]

    return ""
