import time
from datetime import datetime, timedelta, timezone

from agent_host.agents.stock.sources.base import NewsSource
from agent_host.models import DigestItem

BASE = "https://finnhub.io/api/v1"


class FinnhubSource(NewsSource):
    def __init__(self, api_key: str, http=None, *, clock=time.monotonic,
                 sleep=time.sleep, min_interval=1.0, now=None):
        self._api_key = api_key or ""
        self._clock = clock
        self._sleep = sleep
        self._min_interval = min_interval      # 60 calls/min -> >=1s spacing
        self._last: float | None = None
        self._cache: dict[tuple, object] = {}
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._http = http
        if self._http is None and self._api_key:
            import httpx
            self._http = httpx.Client(timeout=15)

    def _space(self) -> None:
        if self._last is not None:
            wait = self._min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _get(self, path: str, params: dict):
        if not self._api_key:
            return None
        key = (path, tuple(sorted(params.items())))
        if key in self._cache:
            return self._cache[key]
        self._space()
        try:
            resp = self._http.get(BASE + path, params={**params, "token": self._api_key})
        except Exception:  # noqa: BLE001 - network dead, source degrades to []
            return None
        self._cache[key] = resp
        return resp

    @staticmethod
    def _ok(resp) -> bool:
        return resp is not None and getattr(resp, "status_code", 0) == 200

    @staticmethod
    def _to_item(d: dict, category: str, symbol: str | None = None) -> DigestItem:
        ts = d.get("datetime")
        published = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
        return DigestItem(
            source="finnhub", category=category, title=d.get("headline", ""),
            url=d.get("url"), summary=d.get("summary"), published_at=published,
            raw={"symbol": symbol} if symbol else {},
        )

    def company_news(self, symbol: str) -> list[DigestItem]:
        to = self._now().date()
        frm = to - timedelta(days=7)
        resp = self._get("/company-news",
                         {"symbol": symbol, "from": frm.isoformat(), "to": to.isoformat()})
        if not self._ok(resp):
            return []
        return [self._to_item(d, "company", symbol) for d in (resp.json() or [])]

    def peers(self, symbol: str) -> list[str]:
        resp = self._get("/stock/peers", {"symbol": symbol})
        if not self._ok(resp):
            return []
        return [str(p) for p in (resp.json() or []) if isinstance(p, str)]

    def market_news(self) -> list[DigestItem]:
        resp = self._get("/news", {"category": "general"})
        if not self._ok(resp):
            return []
        return [self._to_item(d, "market") for d in (resp.json() or [])]

    def earnings_surprises(self, symbol: str) -> list[dict]:
        resp = self._get("/stock/earnings", {"symbol": symbol})
        if not self._ok(resp):
            return []
        return list(resp.json() or [])

    def earnings_calendar(self, symbol: str) -> list[dict]:
        # Forward calendar is likely premium: 403 -> graceful []; forward
        # earnings dates otherwise come from YFinanceSource.earnings_dates().
        resp = self._get("/calendar/earnings", {"symbol": symbol})
        if not self._ok(resp):
            return []
        return list((resp.json() or {}).get("earningsCalendar", []))
