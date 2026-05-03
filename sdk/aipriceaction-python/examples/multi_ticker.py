"""Build AI context for multiple tickers."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Browse available multi-ticker questions
for i, q in enumerate(builder.questions("multi")):
    print(f"  [{i}] {q['title']}: {q['snippet']}")

# Build context for multiple tickers
context = builder.build(tickers=["VCB", "FPT", "TCB"], interval="1D")
print(context)
