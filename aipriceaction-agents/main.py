import time
from langchain_openai import ChatOpenAI

from aipriceaction import AIContextBuilder
from settings import settings


def main():
    t0 = time.perf_counter()

    print("Settings:")
    print(f"  OPENAI_BASE_URL:    {settings.openai_base_url}")
    print(f"  OPENAI_MODEL:       {settings.openai_model}")
    print(f"  AI_CONTEXT_LANG:    {settings.ai_context_lang}")
    print()

    # 1. Build context
    print("[1/3] Building AI context...")
    t1 = time.perf_counter()
    builder = AIContextBuilder(lang=settings.ai_context_lang)
    questions = builder.questions("single")
    for i, q in enumerate(questions):
        print(f"  [{i}] {q['title']}: {q['snippet']}")

    context = builder.build(
        ticker="VCB",
        interval="1D",
        question=questions[0]["question"],
    )
    t2 = time.perf_counter()
    print(f"  Context built in {t2 - t1:.2f}s ({len(context):,} chars)")
    print()

    # 2. Call LLM
    print(f"[2/3] Calling {settings.openai_model}...")
    t3 = time.perf_counter()
    llm = ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        model=settings.openai_model,
    )
    response = llm.invoke(context)
    t4 = time.perf_counter()
    print(f"  LLM responded in {t4 - t3:.2f}s")
    print()

    # 3. Print response
    print("[3/3] Response:")
    print("---")
    print(response.content)
    print("---")
    print()
    print(f"Total: {t4 - t0:.2f}s")


if __name__ == "__main__":
    main()
