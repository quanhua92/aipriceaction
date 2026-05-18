"""Predefined watchlists — hardcoded ticker groups built into the CLI."""

VN30_TICKERS = [
    "ACB", "BID", "BSR", "CTG", "FPT",
    "GAS", "GVR", "HDB", "HPG", "LPB",
    "MBB", "MSN", "MWG", "PLX", "SAB",
    "SHB", "SSB", "SSI", "STB", "TCB",
    "TPB", "VCB", "VHM", "VIB", "VIC",
    "VJC", "VNM", "VPB", "VRE", "VPL",
]

VINGROUP_TICKERS = ["VIC", "VHM", "VRE", "VPL"]

TM_TICKERS = ["GEX", "GEE", "VIX", "EIB", "VGC", "IDC"]

MASAN_TICKERS = ["MSN", "MCH", "MSR", "MML", "VCF", "VSN", "NET"]

INDEX_TICKERS = [
    # MARKET_INDICES (pinned)
    "VNINDEX", "VN30", "VN30F1M",
    # INDEX_TICKERS (regular)
    "VN100", "VNMIDCAP", "VNSMALLCAP", "VNALLSHARE", "VNXALLSHARE",
    "VNFIN", "HNX30", "VNREAL", "VNENE", "VNMITECH", "VNUTI",
    "VNCONS", "VNCOND", "VNHEAL", "VNIND", "VNFINLEAD",
    "VNFINSELECT", "VNDIAMOND", "VNDIVIDEND",
]

CROSS_TICKERS = ["VNINDEX", "^GSPC", "GC=F", "SJC-GOLD", "KC=F", "BZ=F", "BTCUSDT"]

PREDEFINED_WATCHLISTS: dict[str, list[str]] = {
    "VN30": VN30_TICKERS,
    "VINGROUP": VINGROUP_TICKERS,
    "TM": TM_TICKERS,
    "MASAN": MASAN_TICKERS,
    "INDEX": INDEX_TICKERS,
    "CROSS": CROSS_TICKERS,
}

PREDEFINED_DESCRIPTIONS: dict[str, str] = {
    "VN30": "VN30 Index - 30 largest companies by market cap",
    "VINGROUP": "Vingroup ecosystem companies",
    "TM": "TM Group companies",
    "MASAN": "Masan Group companies",
    "INDEX": "Vietnamese market indices",
    "CROSS": "Cross-market instruments",
}


def get_predefined_names() -> list[str]:
    return list(PREDEFINED_WATCHLISTS.keys())


def get_predefined_tickers(name: str) -> list[str]:
    return PREDEFINED_WATCHLISTS.get(name.upper(), [])


def is_predefined(name: str) -> bool:
    return name.upper() in PREDEFINED_WATCHLISTS
