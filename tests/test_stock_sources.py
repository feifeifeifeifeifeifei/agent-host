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
        if self._earnings is None:
            return None
        return FakeEarnings(self._earnings)


def _src(mapping, **kw):
    return YFinanceSource(ticker_factory=lambda s: mapping[s], sleep=lambda _x: None, **kw)


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
