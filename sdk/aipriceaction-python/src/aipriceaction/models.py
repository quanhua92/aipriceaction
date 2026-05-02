from dataclasses import dataclass
from typing import Optional


@dataclass
class TickerInfo:
    source: str
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    type: Optional[str] = None
    category: Optional[str] = None
    group: Optional[str] = None
    groups: Optional[list[str]] = None
