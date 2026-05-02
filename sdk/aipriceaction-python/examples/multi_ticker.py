"""Build AI context for multiple tickers with a custom question."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Browse available multi-ticker questions
for i, q in enumerate(builder.questions("multi")):
    print(f"  [{i}] {q['title']}: {q['snippet']}")

# Build context for multiple tickers
context = builder.build(
    tickers=["VCB", "FPT", "TCB"],
    interval="1D",
    question="Compare the technical strength of these tickers.",
)
print(context)
