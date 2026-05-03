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

    # 1. Build context
    print("[1/4] Building AI context...")
    t1 = time.perf_counter()
    builder = AIContextBuilder(lang=settings.ai_context_lang)
    questions = builder.questions("single")
    for i, q in enumerate(questions):
        print(f"  [{i}] {q['title']}: {q['snippet']}")
    builder.build(ticker="VCB", interval="1D")
    t2 = time.perf_counter()
    context = builder._last_context
    print(f"  Context built in {t2 - t1:.2f}s ({len(context):,} chars)")
    print(f"  VNINDEX in context: {'VNINDEX' in context}")
    print()

    # 2. First LLM call (cold — no KV cache)
    print(f"[2/4] First LLM call ({settings.openai_model})...")
    t3 = time.perf_counter()
    response1 = builder.answer(questions[0]["question"])
    t4 = time.perf_counter()
    print(f"  LLM responded in {t4 - t3:.2f}s (cold)")
    print()

    # 3. Follow-up LLM call (warm — same context prefix, KV cache hit)
    follow_up = "Nhận định VNINDEX tuần tiếp theo"
    print(f"[3/4] Follow-up LLM call: \"{follow_up}\"...")
    t5 = time.perf_counter()
    response2 = builder.answer(follow_up)
    t6 = time.perf_counter()
    print(f"  LLM responded in {t6 - t5:.2f}s (warm, KV cache)")
    print()

    # 4. Print responses
    print("[4/4] Responses:")
    print("--- First call ---")
    print(response1)
    print()
    print("--- Follow-up call ---")
    print(response2)
    print("---")
    print()

    # Timing summary
    print("=" * 50)
    print("TIMING SUMMARY")
    print("=" * 50)
    print(f"  Model:            {settings.openai_model}")
    print(f"  Context length:   {len(context):>8,} chars")
    print(f"  Context build:    {t2 - t1:>8.2f}s")
    print(f"  LLM 1 (cold):     {t4 - t3:>8.2f}s")
    print(f"  LLM 2 (warm):     {t6 - t5:>8.2f}s")
    print(f"  KV cache speedup: {(t4 - t3) / (t6 - t5) if t6 > t5 else 0:>7.1f}x")
    print(f"  Total:            {t6 - t0:>8.2f}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
