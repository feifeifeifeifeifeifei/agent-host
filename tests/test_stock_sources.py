import pytest
from agent_host.agents.stock.sources.base import MarketDataSource, NewsSource


def test_market_data_source_is_abstract():
    with pytest.raises(TypeError):
        MarketDataSource()  # abstract methods unimplemented


def test_news_source_is_abstract():
    with pytest.raises(TypeError):
        NewsSource()


def test_concrete_subclass_satisfies_protocol():
    class M(MarketDataSource):
        def pct_changes(self, symbols): return {}
        def index_levels(self): return {}
        def sector(self, symbol): return None
        def earnings_dates(self, symbol): return []

    class N(NewsSource):
        def company_news(self, symbol): return []
        def peers(self, symbol): return []
        def market_news(self): return []
        def earnings_surprises(self, symbol): return []

    assert M().pct_changes(["AAPL"]) == {}
    assert N().company_news("AAPL") == []


from datetime import datetime, date

from agent_host.agents.stock.sources.yfinance_source import YFinanceSource


class FakeHist:
    def __init__(self, closes): self._c = list(closes)
    @property
    def empty(self): return len(self._c) == 0
    def __getitem__(self, k): return list(self._c)  # "Close" -> list


class FakeEarnings:
    def __init__(self, dates): self.index = list(dates)
    @property
    def empty(self): return len(self.index) == 0


class FakeTicker:
    def __init__(self, closes=None, info=None, earnings=None, boom=False):
        self._closes = closes
        self._info = info or {}
        self._earnings = earnings
        self._boom = boom
    def history(self, period="2d"):
        if self._boom:
            raise RuntimeError("yahoo 429")
        return FakeHist(self._closes if self._closes is not None else [])
    @property
    def info(self):
        if self._boom:
            raise RuntimeError("info blocked")
        return self._info
    def get_earnings_dates(self):
        if self._boom:
            raise RuntimeError("earnings blocked")
        if self._earnings is None:
            return None
        return FakeEarnings(self._earnings)


def _src(mapping, **kw):
    kw.setdefault("max_workers", 1)
    return YFinanceSource(ticker_factory=lambda s: mapping[s], sleep=lambda _x: None, **kw)


def test_map_runs_sync_when_single_worker():
    src = YFinanceSource(ticker_factory=lambda s: None, max_workers=1)
    assert src._map(lambda x: x * 2, [1, 2, 3]) == [2, 4, 6]


def test_map_empty_returns_empty():
    src = YFinanceSource(ticker_factory=lambda s: None, max_workers=8)
    assert src._map(lambda x: x * 2, []) == []


def test_map_threaded_preserves_order_and_values():
    src = YFinanceSource(ticker_factory=lambda s: None, max_workers=8)
    assert src._map(lambda x: x * x, list(range(20))) == [x * x for x in range(20)]


def test_pct_changes_computes_day_pct_and_omits_missing():
    src = _src({
        "AAPL": FakeTicker(closes=[100.0, 104.0]),   # +4.00%
        "MSFT": FakeTicker(closes=[]),               # no data -> omitted
    })
    out = src.pct_changes(["AAPL", "MSFT"])
    assert out["AAPL"] == pytest.approx(4.0)
    assert "MSFT" not in out


def test_pct_changes_tolerates_nan():
    src = _src({"NVDA": FakeTicker(closes=[float("nan"), 100.0, 110.0])})
    assert src.pct_changes(["NVDA"])["NVDA"] == pytest.approx(10.0)


def test_pct_changes_survives_dead_ticker():
    src = _src({"AAPL": FakeTicker(closes=[100.0, 101.0]),
                "BAD": FakeTicker(boom=True)})
    out = src.pct_changes(["AAPL", "BAD"])
    assert out["AAPL"] == pytest.approx(1.0)
    assert "BAD" not in out          # retried then gave up, did not raise


def test_index_levels_includes_tnx_with_divide_by_ten_guard():
    src = _src({
        "^GSPC": FakeTicker(closes=[5000.0, 5050.0]),
        "^IXIC": FakeTicker(closes=[16000.0, 16160.0]),
        "^DJI": FakeTicker(closes=[40000.0, 40000.0]),
        "^SOX": FakeTicker(closes=[5000.0, 5100.0]),
        "^TNX": FakeTicker(closes=[41.5, 42.0]),      # reads ~42 -> /10 -> 4.2
    })
    levels = src.index_levels()
    assert levels["^GSPC"]["pct"] == pytest.approx(1.0)
    assert levels["^TNX"]["level"] == pytest.approx(4.2)
    assert levels["^TNX"]["pct"] == pytest.approx((42.0 - 41.5) / 41.5 * 100.0)


def test_sector_best_effort():
    src = _src({
        "AAPL": FakeTicker(info={"sector": "Technology"}),
        "SPY": FakeTicker(info={}),           # ETF, no sector
        "BAD": FakeTicker(boom=True),
    })
    assert src.sector("AAPL") == "Technology"
    assert src.sector("SPY") is None
    assert src.sector("BAD") is None          # exception -> None


def test_earnings_dates_best_effort():
    src = _src({
        "AAPL": FakeTicker(earnings=[datetime(2026, 8, 5, 16, 0)]),
        "MSFT": FakeTicker(earnings=None),
    })
    assert src.earnings_dates("AAPL") == [date(2026, 8, 5)]
    assert src.earnings_dates("MSFT") == []


def test_earnings_dates_bulk_maps_all_symbols():
    src = _src({
        "AAPL": FakeTicker(earnings=[datetime(2026, 8, 5, 16, 0)]),
        "MSFT": FakeTicker(earnings=None),
    })
    out = src.earnings_dates_bulk(["AAPL", "MSFT"])
    assert out["AAPL"] == [date(2026, 8, 5)]
    assert out["MSFT"] == []


def test_earnings_dates_bulk_survives_dead_symbol():
    src = _src({
        "AAPL": FakeTicker(earnings=[datetime(2026, 8, 5, 16, 0)]),
        "BAD": FakeTicker(boom=True),      # get_earnings_dates path raises upstream
    })
    out = src.earnings_dates_bulk(["AAPL", "BAD"])
    assert out["AAPL"] == [date(2026, 8, 5)]
    assert out["BAD"] == []               # swallowed -> [], batch not crashed


def test_base_earnings_dates_bulk_default_loops():
    class M(MarketDataSource):
        def pct_changes(self, symbols): return {}
        def index_levels(self): return {}
        def sector(self, symbol): return None
        def earnings_dates(self, symbol): return [date(2026, 1, 1)] if symbol == "X" else []
    assert M().earnings_dates_bulk(["X", "Y"]) == {"X": [date(2026, 1, 1)], "Y": []}


def test_prefetch_fetches_each_symbol_once_and_dedupes():
    calls = []

    class CountingTicker:
        def __init__(self, sym): self._sym = sym
        def history(self, period="2d"):
            calls.append(self._sym)
            return FakeHist([100.0, 101.0])

    src = YFinanceSource(ticker_factory=CountingTicker, sleep=lambda _x: None,
                         max_workers=1)
    src._prefetch(["AAPL", "MSFT", "AAPL"])   # duplicate must not refetch
    src._prefetch(["AAPL"])                     # already cached -> no fetch
    assert sorted(calls) == ["AAPL", "MSFT"]


def test_pct_changes_correct_under_real_threads():
    mapping = {f"S{i}": FakeTicker(closes=[100.0, 100.0 + i]) for i in range(20)}
    src = YFinanceSource(ticker_factory=lambda s: mapping[s], sleep=lambda _x: None,
                         max_workers=8)
    out = src.pct_changes(list(mapping))
    assert out["S1"] == pytest.approx(1.0)
    assert out["S10"] == pytest.approx(10.0)
    assert out["S0"] == pytest.approx(0.0)   # 0% change is kept (has 2 closes)


from agent_host.agents.stock.sources.finnhub_source import FinnhubSource


class FakeResp:
    def __init__(self, data, status=200):
        self._d = data
        self.status_code = status
    def json(self):
        return self._d


class FakeHttp:
    def __init__(self, resp_map):
        self._map = resp_map            # url-fragment -> FakeResp
        self.calls = []
    def get(self, url, params=None):
        self.calls.append((url, params))
        for frag, resp in self._map.items():
            if frag in url:
                return resp
        return FakeResp([], 404)


def test_empty_key_disables_all_methods():
    src = FinnhubSource("")            # no key -> disabled, no http needed
    assert src.company_news("AAPL") == []
    assert src.peers("AAPL") == []
    assert src.market_news() == []
    assert src.earnings_surprises("AAPL") == []
    assert src.earnings_calendar("AAPL") == []


def test_company_news_maps_to_digest_items_with_links():
    http = FakeHttp({"/company-news": FakeResp([
        {"headline": "Apple beats", "url": "https://x/1",
         "summary": "Strong iPhone quarter.", "datetime": 1_800_000_000},
    ])})
    src = FinnhubSource("k", http=http, sleep=lambda _x: None)
    items = src.company_news("AAPL")
    assert len(items) == 1
    assert items[0].title == "Apple beats"
    assert items[0].url == "https://x/1"
    assert items[0].summary == "Strong iPhone quarter."
    assert items[0].raw["symbol"] == "AAPL"
    assert items[0].published_at is not None


def test_peers_and_market_news_and_surprises():
    http = FakeHttp({
        "/stock/peers": FakeResp(["AAPL", "MSFT", "GOOGL"]),
        "/news": FakeResp([{"headline": "Markets rally", "url": "u", "datetime": 1}]),
        "/stock/earnings": FakeResp([{"period": "2026-06-30", "surprisePercent": 3.1}]),
    })
    src = FinnhubSource("k", http=http, sleep=lambda _x: None)
    assert src.peers("AAPL") == ["AAPL", "MSFT", "GOOGL"]
    assert src.market_news()[0].title == "Markets rally"
    assert src.earnings_surprises("AAPL")[0]["surprisePercent"] == 3.1


def test_calendar_earnings_premium_403_falls_back_to_empty():
    http = FakeHttp({"/calendar/earnings": FakeResp({"error": "premium"}, status=403)})
    src = FinnhubSource("k", http=http, sleep=lambda _x: None)
    assert src.earnings_calendar("AAPL") == []      # premium -> graceful []


def test_rate_limit_spacing_and_cache():
    http = FakeHttp({
        "/company-news": FakeResp([]),
        "/stock/peers": FakeResp([]),
    })
    slept = []
    src = FinnhubSource("k", http=http, clock=lambda: 0.0,
                        sleep=lambda s: slept.append(s), min_interval=1.0)
    src.company_news("AAPL")            # 1st call: no wait
    src.peers("AAPL")                   # 2nd call: must space >= 1s
    assert slept == [pytest.approx(1.0)]
    # per-run cache: repeating an identical request hits no new http call
    before = len(http.calls)
    src.company_news("AAPL")
    assert len(http.calls) == before
