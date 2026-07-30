import time
from datetime import date

from agent_host.agents.stock.sources.base import MarketDataSource

INDEX_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^SOX", "^TNX")


class YFinanceSource(MarketDataSource):
    def __init__(self, session=None, ticker_factory=None, attempts=3, sleep=time.sleep):
        self._session = session
        self._factory = ticker_factory
        self._attempts = attempts
        self._sleep = sleep
        self._hist_cache: dict[str, object] = {}   # per-run cache

    # --- ticker plumbing -------------------------------------------------
    def _build_factory(self):
        from curl_cffi import requests as crequests   # lazy; native ext
        import yfinance as yf
        session = self._session or crequests.Session(impersonate="chrome")
        return lambda sym: yf.Ticker(sym, session=session)

    def _ticker(self, symbol: str):
        if self._factory is None:
            self._factory = self._build_factory()
        return self._factory(symbol)

    def _history(self, symbol: str, period: str = "2d"):
        if symbol in self._hist_cache:
            return self._hist_cache[symbol]
        for attempt in range(self._attempts):
            try:
                hist = self._ticker(symbol).history(period=period)
                self._hist_cache[symbol] = hist
                return hist
            except Exception:  # noqa: BLE001 - retry w/ backoff, then give up
                if attempt + 1 < self._attempts:
                    self._sleep(0.5 * (2 ** attempt))
        return None

    @staticmethod
    def _clean_closes(hist) -> list[float]:
        if hist is None or getattr(hist, "empty", False):
            return []
        try:
            return [float(c) for c in list(hist["Close"]) if c == c]  # c==c drops NaN
        except Exception:  # noqa: BLE001
            return []

    # --- MarketDataSource ------------------------------------------------
    def pct_changes(self, symbols: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sym in symbols:
            closes = self._clean_closes(self._history(sym))
            if len(closes) < 2 or not closes[-2]:
                continue
            out[sym] = (closes[-1] - closes[-2]) / closes[-2] * 100.0
        return out

    def index_levels(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for sym in INDEX_SYMBOLS:
            closes = self._clean_closes(self._history(sym))
            if not closes:
                continue
            level = closes[-1]
            if sym == "^TNX" and level > 25:   # yield x10 quirk guard
                level = level / 10.0
            pct = None
            if len(closes) >= 2 and closes[-2]:
                pct = (closes[-1] - closes[-2]) / closes[-2] * 100.0
            out[sym] = {"level": level, "pct": pct}
        return out

    def sector(self, symbol: str) -> str | None:
        try:
            info = self._ticker(symbol).info
            return info.get("sector") if info else None
        except Exception:  # noqa: BLE001 - .info is heavy/flaky, best-effort
            return None

    def earnings_dates(self, symbol: str) -> list[date]:
        try:
            df = self._ticker(symbol).get_earnings_dates()
        except Exception:  # noqa: BLE001
            return []
        if df is None or getattr(df, "empty", False):
            return []
        try:
            return [ts.date() if hasattr(ts, "date") else ts for ts in list(df.index)]
        except Exception:  # noqa: BLE001
            return []
