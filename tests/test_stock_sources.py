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
