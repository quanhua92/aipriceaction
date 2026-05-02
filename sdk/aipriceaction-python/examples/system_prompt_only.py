"""Build AI context with system prompt only (no ticker data)."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

context = builder.build()
print(context)
