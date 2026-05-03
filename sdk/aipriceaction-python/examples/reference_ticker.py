"""Build AI context with a reference ticker for market context comparison."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

# VNINDEX is included by default; pass reference_ticker=None to omit
context = builder.build(ticker="VCB", interval="1D")
print(context)
