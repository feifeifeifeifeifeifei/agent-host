# Single-stock leveraged/inverse ETF -> its single underlying stock.
#
# ILLUSTRATIVE, NOT EXHAUSTIVE. The author doesn't trade leveraged ETFs; this map
# exists to demonstrate the "enrich the why with the underlying" pattern. It only
# covers single-stock products (TSLL -> TSLA), never index/sector ones (TQQQ,
# SOXL, UPRO), which have no single underlying stock. Extend it with the tickers
# you actually hold.
LEVERAGED_UNDERLYING: dict[str, str] = {
    "PLTU": "PLTR",
    "TSLL": "TSLA", "TSLR": "TSLA", "TSLT": "TSLA",
    "NVDL": "NVDA", "NVDU": "NVDA", "NVDX": "NVDA",
    "AAPU": "AAPL", "AAPB": "AAPL",
    "MSFU": "MSFT",
    "GGLL": "GOOGL",
    "AMZU": "AMZN",
    "METU": "META",
    "MSTU": "MSTR", "MSTX": "MSTR",
    "CONL": "COIN",
    "AMDL": "AMD",
}


def catalyst_symbol(sym: str) -> str:
    """The symbol whose fundamentals (news/earnings) drive `sym`: the underlying
    for a mapped single-stock leveraged ETF, else `sym` itself."""
    return LEVERAGED_UNDERLYING.get(sym, sym)


def is_leveraged(sym: str) -> bool:
    """True iff `sym` is a mapped single-stock leveraged ETF."""
    return sym in LEVERAGED_UNDERLYING
