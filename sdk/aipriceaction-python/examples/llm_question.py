"""Build AI context and call LLM with follow-up questions.

Demonstrates KV cache reuse: the second answer() call is faster because
the context prefix is cached by the LLM provider.
"""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# 1. Build context once
print("[1] Building context...")
builder.build(ticker="VCB", interval="1D")
print(f"    Context: {len(builder._last_context):,} chars\n")

# 2. Ask a question
print("[2] Asking question...")
response1 = builder.answer("What is the current trend?")
print(f"    Answer: {response1[:200]}...\n")

# 3. Follow-up (same context, faster due to KV cache)
print("[3] Follow-up question...")
response2 = builder.answer("What is the support level?")
print(f"    Answer: {response2[:200]}...\n")
