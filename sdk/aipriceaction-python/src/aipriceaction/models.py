from dataclasses import dataclass


@dataclass
class TickerInfo:
    source: str
    ticker: str
    name: str | None = None
    exchange: str | None = None
    group: str | None = None
