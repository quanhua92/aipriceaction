"""The exact concurrent API routes requested by the web home page."""

CORE_HOME_PAGE_URLS = (
    "/tickers?limit=1&ma=true&ema=true&mode=vn&format=csv",
    "/tickers?limit=1&ma=true&ema=true&mode=crypto&format=csv",
    "/tickers?limit=1&ma=true&ema=true&mode=yahoo&format=csv",
    "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true",
    "/tickers?interval=1D&limit=1&mode=vn&ema=true&format=csv",
    "/health",
    "/tickers/group",
    "/tickers?limit=1&ma=false&mode=vn&format=csv",
    "/tickers/group?mode=crypto",
    "/tickers?limit=1&ma=false&mode=crypto&format=csv",
    "/tickers/group?mode=yahoo",
    "/tickers?limit=1&ma=false&mode=yahoo&format=csv",
    "/tickers/name",
    "/tickers/name?mode=crypto",
    "/tickers/name?mode=yahoo",
    "/analysis/rrg?algorithm=jdk&mode=vn&trails=5&min_volume=1000&benchmark=VNINDEX&period=10&date=2026-10-21&ema=true",
    "/tickers?limit=1&ma=false&mode=vn&format=csv",
    "/tickers?limit=1&ma=false&mode=crypto&format=csv",
    "/tickers?limit=1&ma=false&mode=yahoo&format=csv",
)
