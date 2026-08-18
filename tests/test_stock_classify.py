from agent_host.agents.stock.classify import TickerClass, classify


class FakeUniverse:
    def __init__(self, types): self._t = types
    def is_listed(self, s): return s in self._t
    def symbol_type(self, s): return self._t.get(s)


def test_equity_gets_sector_and_capped_peers():
    uni = FakeUniverse({"AAPL": "equity"})
    tc = classify(
        "AAPL", uni,
        info_fn=lambda s: "Technology",
        peers_fn=lambda s: ["MSFT", "GOOGL", "AAPL", "AMZN", "META", "NVDA"],
        peer_limit=5,
    )
    assert tc.kind == "equity"
    assert tc.sector == "Technology"
    assert "AAPL" not in tc.peers              # self excluded
    assert tc.peers == ["MSFT", "GOOGL", "AMZN", "META", "NVDA"]   # capped at 5


def test_etf_maps_to_sector_theme_no_peers():
    uni = FakeUniverse({"SOXX": "etf"})
    tc = classify("SOXX", uni, info_fn=lambda s: "Technology")
    assert tc.kind == "etf"
    assert tc.theme == "Technology"
    assert tc.peers == []


def test_index_and_rate_get_macro_theme():
    uni = FakeUniverse({"^GSPC": "index", "^TNX": "index"})
    idx = classify("^GSPC", uni)
    rate = classify("^TNX", uni)
    assert idx.kind == "index" and idx.peers == [] and idx.theme
    assert rate.kind == "rate"
    assert "rate" in rate.theme.lower()


def test_future_gets_macro_theme_no_peers():
    uni = FakeUniverse({"CL=F": "future"})
    tc = classify("CL=F", uni)
    assert tc.kind == "future"
    assert "oil" in tc.theme.lower()
    assert tc.peers == []


def test_peers_fn_failure_degrades_to_empty():
    uni = FakeUniverse({"AAPL": "equity"})
    def boom(_s): raise RuntimeError("finnhub down")
    tc = classify("AAPL", uni, info_fn=lambda s: None, peers_fn=boom)
    assert tc.kind == "equity"
    assert tc.peers == []


def test_returns_dataclass_instance():
    uni = FakeUniverse({"AAPL": "equity"})
    assert isinstance(classify("AAPL", uni), TickerClass)
