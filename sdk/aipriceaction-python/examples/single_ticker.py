"""Build AI context for a single ticker with a question from the question bank."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# Browse available questions
for i, q in enumerate(builder.questions("single")):
    print(f"  [{i}] {q['title']}: {q['snippet']}")

# Build context with the first template question
context = builder.build(
    ticker="VCB",
    interval="1D",
    question=builder.questions("single")[0]["question"],
)
print(context)
