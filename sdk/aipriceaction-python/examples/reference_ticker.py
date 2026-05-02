"""Build AI context with a reference ticker for market context comparison."""

from aipriceaction import AIContextBuilder

builder = AIContextBuilder(lang="en")

context = builder.build(
    ticker="VCB",
    interval="1D",
    reference_ticker="VNINDEX",
    question="Analyze VCB relative to the overall market trend.",
)
print(context)
