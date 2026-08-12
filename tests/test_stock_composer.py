from agent_host.agents.stock.composer import RecapData, StockComposer
from agent_host.models import DigestItem


def _c(recap):
    return StockComposer("en").compose(recap)


def test_full_recap_renders_fixed_structure():
    out = _c(RecapData(
        title="US Market Recap · Aug 6, 2026",
        indices=[{"symbol": "^GSPC", "name": "S&P 500", "level": 7723.55, "pct": -0.17}],
        movers=[
            {"symbol": "MP", "pct": 8.28, "catalyst": "MP", "cause_kind": "none", "headline": None},
            {"symbol": "GOOG", "pct": -4.05, "catalyst": "GOOG", "cause_kind": "news",
             "headline": DigestItem(source="finnhub", title="Alphabet news", url="https://x/g")},
            {"symbol": "DDOG", "pct": -19.03, "catalyst": "DDOG", "cause_kind": "earnings",
             "headline": None},
        ],
        earnings=[{"symbol": "DDOG", "note": "reported earnings"}],
        market_news=[DigestItem(source="finnhub", title="Fed holds rates", url="https://x/fed")],
    ))
    assert "<b>US Market Recap · Aug 6, 2026</b>" in out
    assert "S&amp;P 500 (^GSPC): -0.17% to 7723.55" in out
    assert "no clear catalyst (likely sector/technical)" in out
    assert '<a href="https://x/g">Alphabet news</a>' in out
    assert "reported earnings" in out
    assert '<b>Market Headlines</b>' in out and '<a href="https://x/fed">Fed holds rates</a>' in out


def test_leveraged_mover_lines_reference_catalyst():
    out = _c(RecapData(
        title="T",
        indices=[{"symbol": "^GSPC", "name": "S&P 500", "level": 1.0, "pct": 1.0}],
        movers=[
            {"symbol": "PLTU", "pct": 6.0, "catalyst": "PLTR", "cause_kind": "news",
             "headline": DigestItem(source="finnhub", title="Palantir wins contract",
                                    url="https://x/pltr")},
            {"symbol": "TSLL", "pct": 7.0, "catalyst": "TSLA", "cause_kind": "earnings",
             "headline": None},
        ],
        earnings=[],
        market_news=[],
    ))
    assert "recent PLTR headline:" in out
    assert '<a href="https://x/pltr">Palantir wins contract</a>' in out
    assert "reported earnings (via TSLA)" in out


def test_indices_unavailable_when_empty():
    out = _c(RecapData(title="T", indices=[], movers=[], earnings=[], market_news=[]))
    assert "<b>Indices</b>\nunavailable today" in out


def test_market_mode_has_no_movers_or_earnings():
    out = _c(RecapData(title="T", indices=[{"symbol": "^GSPC", "name": "S&P 500",
                                            "level": 1.0, "pct": 1.0}],
                       movers=[], earnings=[],
                       market_news=[DigestItem(source="finnhub", title="Macro", url="u")],
                       personalized=False))
    assert "Notable Movers" not in out and "Earnings" not in out
    assert "Market Headlines" in out


def test_all_empty_returns_no_data():
    out = _c(RecapData(title="T", indices=[], movers=[], earnings=[], market_news=[],
                       personalized=False))
    assert out == "<b>No market data available today.</b>"


def test_escapes_fetched_text():
    out = _c(RecapData(title="T", indices=[], movers=[], earnings=[],
                       market_news=[DigestItem(source="finnhub",
                                               title="<script>alert(1)</script>", url="u")]))
    assert "<script>" not in out and "&lt;script&gt;" in out
