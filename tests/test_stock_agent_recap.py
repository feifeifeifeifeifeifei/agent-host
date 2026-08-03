from datetime import date, datetime

from agent_host.agents.stock.agent import StockAgent
from agent_host.channels.telegram import TelegramChannel
from agent_host.services import Services
from agent_host.models import DigestItem


class FakeMarket:
    def __init__(self, pct=None, indices=None, earnings=None, boom_indices=False):
        self._pct = pct or {}
        self._indices = indices or {}
        self._earn = earnings or {}
        self._boom_indices = boom_indices
        self.bulk_calls = 0
        self.single_calls = 0
    def pct_changes(self, symbols):
        return {s: self._pct[s] for s in symbols if s in self._pct}
    def index_levels(self):
        if self._boom_indices:
            raise RuntimeError("yfinance down")
        return self._indices
    def sector(self, s): return None
    def earnings_dates(self, s):
        self.single_calls += 1
        return self._earn.get(s, [])

    def earnings_dates_bulk(self, symbols):
        self.bulk_calls += 1
        return {s: self.earnings_dates(s) for s in symbols}


class FakeNews:
    def __init__(self, company=None, market=None):
        self._c = company or {}
        self._m = market or []
    def company_news(self, s): return self._c.get(s, [])
    def peers(self, s): return []
    def market_news(self): return list(self._m)
    def earnings_surprises(self, s): return []


class FakeUniverse:
    def is_listed(self, s): return True
    def symbol_type(self, s): return "equity"


class FakeWatchlist:
    def __init__(self, tickers): self._t = tickers
    def get(self): return list(self._t)


class CapturingComposer:
    def __init__(self): self.recap = None
    def compose(self, recap):
        self.recap = recap
        return "<b>RECAP</b>"


class MemStore:
    def __init__(self): self._seen = set(); self.runs = []; self._prefs = {}
    def namespaced(self, a): return self
    def seen(self, k): return k in self._seen
    def mark_seen(self, ks): self._seen.update(ks)
    def get_prefs(self, c): return dict(self._prefs)
    def set_prefs(self, c, p): self._prefs = dict(p)
    def record_run(self, meta): self.runs.append(meta)


class Cfg:
    telegram_chat_id = "42"
    finnhub_api_key = ""
    stock_max_tickers = 50
    stock_mover_threshold_pct = 4.0
    stock_max_movers = 5
    stock_peer_limit = 5
    stock_schedule_tz = "America/Vancouver"
    output_language = "en"


def _svc(store):
    return Services(channel=TelegramChannel("t", "42", dry_run=True),
                    llm=object(), store=store, config=Cfg())


def _agent(**overrides):
    defaults = dict(
        market=FakeMarket(indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(),
        universe=FakeUniverse(),
        watchlist_factory=lambda: FakeWatchlist([]),
        composer_factory=lambda: CapturingComposer(),
        is_trading_day=lambda d: True,
        today_fn=lambda tz: date(2026, 7, 29),
    )
    defaults.update(overrides)
    return StockAgent(**defaults)


def test_holiday_gate_sends_nothing_and_records_nothing():
    store = MemStore()
    svc = _svc(store)
    _agent(is_trading_day=lambda d: False).run_scheduled(svc)
    assert svc.channel.sent == []
    assert store.runs == []


def test_personalized_mode_selects_notable_movers_sorted():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(
            pct={"AAPL": 5.2, "MSFT": 1.0, "NVDA": -6.3},   # MSFT below threshold
            indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"AAPL": [DigestItem(source="finnhub", title="up",
                                                   url="u")]}),
        watchlist_factory=lambda: FakeWatchlist(["AAPL", "MSFT", "NVDA"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    syms = [m["symbol"] for m in cc.recap.movers]
    assert set(syms) == {"AAPL", "NVDA"}         # MSFT (1.0%) excluded
    assert syms[0] == "NVDA"                       # |6.3| sorts before |5.2|
    assert svc.channel.sent[-1]["text"] == "<b>RECAP</b>"
    assert store.runs[-1]["mode"] == "personalized"


def test_default_market_mode_when_pool_empty():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(market=[DigestItem(source="finnhub", title="Macro move")]),
        watchlist_factory=lambda: FakeWatchlist([]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    assert cc.recap.movers == []
    assert len(cc.recap.news) == 1
    assert cc.recap.indices[0]["symbol"] == "^GSPC"
    assert store.runs[-1]["mode"] == "market"


def test_earnings_today_flows_into_recap_and_why():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(
            pct={"AAPL": 5.0},
            indices={"^GSPC": {"level": 5050.0, "pct": 1.0}},
            earnings={"AAPL": [date(2026, 7, 29)]}),
        watchlist_factory=lambda: FakeWatchlist(["AAPL"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    assert cc.recap.earnings == [{"symbol": "AAPL", "note": "reports earnings today"}]
    assert cc.recap.why["AAPL"] == "earnings report"


def test_agent_gathers_earnings_via_bulk_call():
    store = MemStore()
    svc = _svc(store)
    market = FakeMarket(pct={"AAPL": 5.0},
                        indices={"^GSPC": {"level": 5050.0, "pct": 1.0}},
                        earnings={"AAPL": [date(2026, 7, 29)]})
    agent = _agent(market=market,
                   watchlist_factory=lambda: FakeWatchlist(["AAPL"]),
                   composer_factory=lambda: CapturingComposer())
    agent.run_scheduled(svc)
    assert market.bulk_calls == 1        # earnings gathered in one bulk call


def test_dead_source_does_not_kill_the_recap():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"AAPL": 5.0}, boom_indices=True),  # index_levels raises
        watchlist_factory=lambda: FakeWatchlist(["AAPL"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    assert cc.recap.indices == []                # dead source degraded to empty
    assert svc.channel.sent[-1]["text"] == "<b>RECAP</b>"
    assert store.runs[-1]["mode"] == "personalized"


def test_agent_static_attrs():
    a = StockAgent()
    assert a.name == "stock"
    assert set(a.commands) == {"/tickers", "/add", "/remove", "/reset",
                               "/help", "/confirm", "/cancel"}
