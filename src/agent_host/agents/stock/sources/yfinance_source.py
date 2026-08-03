import time
from datetime import date

from agent_host.agents.stock.sources.base import MarketDataSource

INDEX_SYMBOLS = ("^GSPC", "^IXIC", "^DJI", "^SOX", "^TNX")


class YFinanceSource(MarketDataSource):
    def __init__(self, session=None, ticker_factory=None, attempts=3, sleep=time.sleep,
                 max_workers=8):
        self._session = session
        self._factory = ticker_factory
        self._attempts = attempts
        self._sleep = sleep
        self._max_workers = max_workers
        self._hist_cache: dict[str, object] = {}   # per-run cache

    # --- ticker plumbing -------------------------------------------------
    def _build_factory(self):
        import yfinance as yf
        if self._session is not None:
            # Caller supplied a session and thereby opted into sharing it.
            session = self._session
            return lambda sym: yf.Ticker(sym, session=session)
        # No injected session: give each worker thread its OWN impersonated
        # curl_cffi session. curl_cffi sessions are not safe to share across
        # threads doing concurrent requests, and _map fans _fetch_history /
        # earnings_dates out across up to max_workers threads.
        import threading
        from curl_cffi import requests as crequests   # lazy; native ext
        local = threading.local()

        def factory(sym):
            session = getattr(local, "session", None)
            if session is None:
                session = crequests.Session(impersonate="chrome")
                local.session = session
            return yf.Ticker(sym, session=session)

        return factory

    def _ensure_factory(self):
        if self._factory is None:
            self._factory = self._build_factory()

    def _ticker(self, symbol: str):
        self._ensure_factory()
        return self._factory(symbol)

    def _map(self, fn, items):
        items = list(items)
        if not items:
            return []
        if self._max_workers <= 1 or len(items) == 1:
            return [fn(x) for x in items]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(items))) as ex:
            return list(ex.map(fn, items))

    def _fetch_history(self, symbol: str, period: str = "2d"):
        for attempt in range(self._attempts):
            try:
                return self._ticker(symbol).history(period=period)
            except Exception:  # noqa: BLE001 - retry w/ backoff, then give up
                if attempt + 1 < self._attempts:
                    self._sleep(0.5 * (2 ** attempt))
        return None

    def _prefetch(self, symbols) -> None:
        # dict.fromkeys dedupes while preserving order, so a symbol listed twice
        # (or passed again after caching) is fetched at most once.
        todo = [s for s in dict.fromkeys(symbols) if s not in self._hist_cache]
        if not todo:
            return
        self._ensure_factory()   # build session on the main thread, pre-race
        for sym, hist in self._map(lambda s: (s, self._fetch_history(s)), todo):
            self._hist_cache[sym] = hist

    def _history(self, symbol: str, period: str = "2d"):
        if symbol not in self._hist_cache:
            self._hist_cache[symbol] = self._fetch_history(symbol, period)
        return self._hist_cache[symbol]

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
        self._prefetch(symbols)
        out: dict[str, float] = {}
        for sym in symbols:
            closes = self._clean_closes(self._history(sym))
            if len(closes) < 2 or not closes[-2]:
                continue
            out[sym] = (closes[-1] - closes[-2]) / closes[-2] * 100.0
        return out

    def index_levels(self) -> dict[str, dict]:
        self._prefetch(INDEX_SYMBOLS)
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

    def earnings_dates_bulk(self, symbols: list[str]) -> dict[str, list[date]]:
        symbols = list(symbols)
        if symbols:
            self._ensure_factory()   # build session on the main thread, pre-race
        return dict(self._map(lambda s: (s, self.earnings_dates(s)), symbols))
