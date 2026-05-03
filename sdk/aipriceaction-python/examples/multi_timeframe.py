"""Multi-timeframe analysis: daily big picture + hourly intraday.

Demonstrates passing previous responses as history so the LLM can
cross-reference timeframes when switching from 1D to 1h.
"""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# 1. Daily context — big picture
print("[1] Building daily context (1D)...")
builder.build(ticker="VIC", interval="1D")
print(f"    Context: {len(builder._last_context):,} chars\n")

# 2. Ask about weekly trend
print("[2] Daily LLM call...")
daily_response = builder.answer("What is the weekly trend?")
print(f"    Answer: {daily_response[:200]}...\n")

# 3. Hourly context — intraday detail, with daily analysis as history
print("[3] Building hourly context (1h)...")
builder.build(ticker="VIC", interval="1h")
print(f"    Context: {len(builder._last_context):,} chars\n")

# 4. Ask intraday question, referencing daily analysis
print("[4] Hourly LLM call with daily history...")
hourly_response = builder.answer(
    "Confirm or reject the daily trend using intraday data.",
    history=[daily_response],
)
print(f"    Answer: {hourly_response[:200]}...\n")
