from dataclasses import dataclass

_FUTURE_THEMES = {
    "CL=F": "energy / oil prices / inflation transmission",
    "GC=F": "precious metals / real rates / inflation",
    "SI=F": "precious & industrial metals / inflation",
    "ZS=F": "agriculture / soft commodities / food inflation",
}
_INDEX_THEME = "broad market / macro breadth"
_RATE_THEME = "Fed policy, interest rates, inflation"


@dataclass
class TickerClass:
    symbol: str
    kind: str                 # "equity" | "etf" | "index" | "rate" | "future"
    sector: str | None
    peers: list[str]
    theme: str | None


def _safe_call(fn, symbol):
    if fn is None:
        return None
    try:
        return fn(symbol)
    except Exception:  # noqa: BLE001 - classification is best-effort
        return None


def classify(symbol, universe, *, info_fn=None, peers_fn=None, peer_limit=5) -> TickerClass:
    sym = symbol
    # Index / rate — shape is authoritative for '^'-prefixed symbols.
    if sym.startswith("^"):
        if sym == "^TNX":
            return TickerClass(sym, "rate", None, [], _RATE_THEME)
        return TickerClass(sym, "index", None, [], _INDEX_THEME)
    # Commodity future.
    if sym.endswith("=F"):
        theme = _FUTURE_THEMES.get(sym, "commodities / macro")
        return TickerClass(sym, "future", None, [], theme)

    stype = universe.symbol_type(sym)
    if stype == "etf":
        sector = _safe_call(info_fn, sym)
        return TickerClass(sym, "etf", sector, [], sector or "sector / thematic ETF")

    # Default: common equity (covers stype == "equity" and unknown-but-listed).
    sector = _safe_call(info_fn, sym)
    peers: list[str] = []
    raw_peers = _safe_call(peers_fn, sym)
    if raw_peers:
        peers = [p for p in raw_peers if p != sym][:peer_limit]
    return TickerClass(sym, "equity", sector, peers, None)
