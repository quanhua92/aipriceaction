import time
from aipriceaction import AIContextBuilder
from aipriceaction.llm_models import OpenRouter
from aipriceaction.settings import settings


def main():
    t0 = time.perf_counter()

    print(f"Settings:")
    print(f"  OPENAI_BASE_URL:    {settings.openai_base_url}")
    print(f"  OPENAI_MODEL:       {settings.openai_model}")
    print(f"  AI_CONTEXT_LANG:    {settings.ai_context_lang}")
    print()

    print("OpenRouter model suggestions:")
    for m in OpenRouter.FREE:
        print(f"  {m.id:65s} {m.label}")
    print()

    builder = AIContextBuilder(lang=settings.ai_context_lang)

    # 1. Daily timeframe — big picture
    print("[1/5] Building daily context (1D)...")
    t1 = time.perf_counter()
    builder.build(ticker="VIC", interval="1D")
    t2 = time.perf_counter()
    context_daily = builder._last_context
    print(f"  Context built in {t2 - t1:.2f}s ({len(context_daily):,} chars)")
    print(f"  VNINDEX in context: {'VNINDEX' in context_daily}")
    print()

    # 2. LLM call on daily
    print(f"[2/5] Daily LLM call ({settings.openai_model})...")
    t3 = time.perf_counter()
    questions = builder.questions("single")
    daily_response = builder.answer(questions[0]["question"])
    t4 = time.perf_counter()
    print(f"  LLM responded in {t4 - t3:.2f}s")
    print()

    # 3. Hourly timeframe — intraday, with daily analysis as history
    print("[3/5] Building hourly context (1h)...")
    t5 = time.perf_counter()
    builder.build(ticker="VIC", interval="1h")
    t6 = time.perf_counter()
    context_hourly = builder._last_context
    print(f"  Context built in {t6 - t5:.2f}s ({len(context_hourly):,} chars)")
    print()

    # 4. LLM call on hourly with daily response as history
    follow_up = "Phân tích khung 1h, xác nhận hoặc bác bỏ xu hướng ngày"
    print(f"[4/5] Hourly LLM call with daily history: \"{follow_up}\"...")
    t7 = time.perf_counter()
    hourly_response = builder.answer(follow_up, history=[daily_response])
    t8 = time.perf_counter()
    print(f"  LLM responded in {t8 - t7:.2f}s")
    print()

    # 5. Print responses
    print("[5/5] Responses:")
    print("--- Daily (1D) ---")
    print(daily_response)
    print()
    print("--- Hourly (1h) + daily history ---")
    print(hourly_response)
    print("---")
    print()

    # Timing summary
    print("=" * 50)
    print("TIMING SUMMARY")
    print("=" * 50)
    print(f"  Model:              {settings.openai_model}")
    print(f"  Daily context:      {len(context_daily):>8,} chars")
    print(f"  Hourly context:     {len(context_hourly):>8,} chars")
    print(f"  Context build 1D:   {t2 - t1:>8.2f}s")
    print(f"  Context build 1h:   {t6 - t5:>8.2f}s")
    print(f"  LLM daily:          {t4 - t3:>8.2f}s")
    print(f"  LLM hourly+history: {t8 - t7:>8.2f}s")
    print(f"  Total:              {t8 - t0:>8.2f}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
