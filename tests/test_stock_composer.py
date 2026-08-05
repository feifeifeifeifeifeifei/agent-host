from agent_host.agents.stock.composer import RecapData, StockComposer
from agent_host.models import DigestItem


class FakeLLM:
    def __init__(self, out="<b>RECAP</b>"):
        self.out = out
        self.messages = None
    def complete(self, messages):
        self.messages = messages
        return self.out

    @property
    def user_content(self):
        return self.messages[-1]["content"] if self.messages else ""


def _full_recap():
    return RecapData(
        indices=[{"symbol": "^GSPC", "name": "S&P 500", "level": 5050.0, "pct": 1.0}],
        movers=[{"symbol": "NVDA", "pct": -6.3, "cause": 'news: "Nvidia slips"',
                 "headlines": [DigestItem(source="finnhub", title="Nvidia slips",
                                          url="https://x/1", summary="Guidance light.")]},
                {"symbol": "AAPL", "pct": 5.2, "cause": "earnings report", "headlines": []}],
        earnings=[{"symbol": "AAPL", "note": "reports earnings today"}],
    )


def test_compose_returns_llm_output_and_feeds_all_sections():
    llm = FakeLLM()
    out = StockComposer(llm, "en").compose(_full_recap())
    assert out == "<b>RECAP</b>"
    body = llm.user_content
    for header in ("INDICES", "MOVERS", "EARNINGS"):
        assert header in body
    assert "cause:" in body                             # per-mover cause label is rendered
    assert "earnings report" in body                    # AAPL's cause (no quotes, survives escaping)
    assert "Nvidia slips: Guidance light." in body      # NVDA's own headline, grouped under it


def test_compose_omits_empty_sections():
    recap = RecapData(
        indices=[{"symbol": "^GSPC", "name": "S&P 500", "level": 5050.0, "pct": 1.0}],
        movers=[], earnings=[],
    )
    llm = FakeLLM()
    StockComposer(llm, "en").compose(recap)
    body = llm.user_content
    assert "INDICES" in body
    assert "MOVERS" not in body
    assert "MARKET NEWS" not in body
    assert "EARNINGS" not in body


def test_compose_escapes_fetched_text():
    recap = RecapData(
        indices=[], movers=[], earnings=[],
        market_news=[DigestItem(source="finnhub",
                                title="<script>alert(1)</script>",
                                url="https://x", summary="a & b")],
    )
    llm = FakeLLM()
    StockComposer(llm, "en").compose(recap)
    body = llm.user_content
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "a &amp; b" in body


def test_compose_all_empty_returns_fixed_string_without_llm_call():
    llm = FakeLLM()
    out = StockComposer(llm, "en").compose(
        RecapData(indices=[], movers=[], earnings=[]))
    assert out == "<b>No market data available today.</b>"
    assert llm.messages is None            # LLM never invoked


def test_index_level_none_does_not_crash():
    recap = RecapData(
        indices=[{"symbol": "^DJI", "name": "Dow Jones", "level": None, "pct": None}],
        movers=[], earnings=[],
    )
    llm = FakeLLM()
    StockComposer(llm, "en").compose(recap)
    assert "n/a" in llm.user_content


def test_compose_renders_market_news_section():
    recap = RecapData(indices=[], movers=[], earnings=[],
                      market_news=[DigestItem(source="finnhub", title="Macro move", url="u")])
    llm = FakeLLM()
    StockComposer(llm, "en").compose(recap)
    assert "MARKET NEWS" in llm.user_content
