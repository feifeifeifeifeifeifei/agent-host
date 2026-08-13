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
    assert len(cc.recap.market_news) == 1
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
    assert cc.recap.earnings == [{"symbol": "AAPL", "note": "reported earnings"}]
    aapl = next(m for m in cc.recap.movers if m["symbol"] == "AAPL")
    assert aapl["cause_kind"] == "earnings"


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


def test_leveraged_mover_why_and_news_come_from_underlying():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"PLTU": 6.0},                     # 2x PLTR, a mover
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"PLTR": [DigestItem(source="finnhub",
                                                   title="Palantir wins contract",
                                                   url="https://x/pltr")]}),
        watchlist_factory=lambda: FakeWatchlist(["PLTU"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    pltu = next(m for m in cc.recap.movers if m["symbol"] == "PLTU")
    assert pltu["catalyst"] == "PLTR"
    assert pltu["cause_kind"] == "news"
    assert pltu["headline"].url == "https://x/pltr"


def test_leveraged_mover_earnings_come_from_underlying():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"PLTU": 6.0},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}},
                          earnings={"PLTR": [date(2026, 7, 29)]}),   # underlying reports today
        watchlist_factory=lambda: FakeWatchlist(["PLTU"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    assert cc.recap.earnings == [{"symbol": "PLTU",
                                  "note": "reported earnings (via PLTR)"}]
    pltu = next(m for m in cc.recap.movers if m["symbol"] == "PLTU")
    assert pltu["catalyst"] == "PLTR"
    assert pltu["cause_kind"] == "earnings"


def test_two_leveraged_etfs_same_underlying_each_get_its_news():
    store = MemStore()
    svc = _svc(store)
    cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"TSLL": 6.0, "TSLR": 7.0},          # both 2x TSLA
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"TSLA": [DigestItem(source="finnhub",
                                                   title="Tesla delivers",
                                                   url="https://x/tsla")]}),
        watchlist_factory=lambda: FakeWatchlist(["TSLL", "TSLR"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    by = {m["symbol"]: m for m in cc.recap.movers}
    assert by["TSLL"]["cause_kind"] == "news"
    assert by["TSLR"]["cause_kind"] == "news"
    assert by["TSLL"]["headline"].url == "https://x/tsla"
    assert by["TSLR"]["headline"].url == "https://x/tsla"


def test_mover_with_no_news_reads_no_clear_catalyst():
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"MP": 8.3},                      # a mover, but…
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={}),                              # …no news, no earnings
        watchlist_factory=lambda: FakeWatchlist(["MP"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    mp = next(m for m in cc.recap.movers if m["symbol"] == "MP")
    assert mp["cause_kind"] == "none"
    assert mp["headline"] is None


def test_mover_with_news_cause_is_its_own_top_headline():
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"MRVL": 12.8},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"MRVL": [DigestItem(source="finnhub",
                                                   title="US bars China optical procurement",
                                                   url="https://x/mrvl")]}),
        watchlist_factory=lambda: FakeWatchlist(["MRVL"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    mrvl = next(m for m in cc.recap.movers if m["symbol"] == "MRVL")
    assert mrvl["cause_kind"] == "news"
    assert mrvl["headline"].url == "https://x/mrvl"


def test_movers_news_is_not_cross_wired():
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"MRVL": 12.8, "MU": 7.6},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"MU": [DigestItem(source="finnhub", title="AI memory demand",
                                                 url="https://x/mu")]}),   # only MU has news
        watchlist_factory=lambda: FakeWatchlist(["MRVL", "MU"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    by = {m["symbol"]: m for m in cc.recap.movers}
    assert by["MU"]["cause_kind"] == "news"
    assert by["MU"]["headline"].url == "https://x/mu"
    assert by["MRVL"]["cause_kind"] == "none"   # not MU's
    assert by["MRVL"]["headline"] is None


def test_agent_static_attrs():
    a = StockAgent()
    assert a.name == "stock"
    assert set(a.commands) == {"/tickers", "/add", "/remove", "/reset",
                               "/help", "/confirm", "/cancel"}


def test_earnings_window_catches_prior_trading_day():
    # today = Thu 2026-08-06; prior session Wed 2026-08-05. DDOG reported after
    # close 08-05; the 08-06 recap must flag it as earnings.
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"DDOG": -19.0},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}},
                          earnings={"DDOG": [date(2026, 8, 5)]}),   # prior trading day
        watchlist_factory=lambda: FakeWatchlist(["DDOG"]),
        composer_factory=lambda: cc,
        today_fn=lambda tz: date(2026, 8, 6),
    )
    agent.run_scheduled(svc)
    ddog = next(m for m in cc.recap.movers if m["symbol"] == "DDOG")
    assert ddog["cause_kind"] == "earnings"
    assert cc.recap.earnings and cc.recap.earnings[0]["symbol"] == "DDOG"


def test_roundup_headline_not_used_as_mover_cause():
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    roundup = DigestItem(source="finnhub",
                         title="Tuesday's session: top gainers and losers in the S&P500",
                         url="https://x/roundup")
    agent = _agent(
        market=FakeMarket(pct={"DDOG": -5.4},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"DDOG": [roundup]}),
        watchlist_factory=lambda: FakeWatchlist(["DDOG"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    ddog = next(m for m in cc.recap.movers if m["symbol"] == "DDOG")
    assert ddog["cause_kind"] == "none"          # roundup rejected as a cause
    assert ddog["headline"] is None


class _SummaryLLM:
    def __init__(self, out="Stocks rose on tame inflation."):
        self.out = out
        self.calls = []
    def complete(self, messages, model=None):
        self.calls.append(model)
        return self.out


def test_opus_summary_appended_when_model_configured():
    store = MemStore(); svc = _svc(store)
    svc.config.stock_summary_model = "anthropic/opus-test"      # enable
    svc.llm = _SummaryLLM()
    cc = CapturingComposer()
    agent = _agent(market=FakeMarket(indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
                   watchlist_factory=lambda: FakeWatchlist([]),   # market mode: simplest path
                   composer_factory=lambda: cc)
    agent.run_scheduled(svc)
    sent = svc.channel.sent[-1]["text"]
    assert "<b>Today's Summary</b>" in sent
    assert sent.startswith("<b>RECAP</b>")                       # recap first, summary appended after
    assert svc.llm.calls == ["anthropic/opus-test"]             # used the configured model


def test_opus_summary_output_is_html_escaped():
    # The model returns plain prose that happens to contain bare HTML-special
    # characters (very likely, e.g. "S&P 500"). Since we send with
    # parse_mode=HTML, anything the model writes must be escaped by us before
    # it reaches Telegram, or the whole message (recap + summary) fails to send.
    store = MemStore(); svc = _svc(store)
    svc.config.stock_summary_model = "anthropic/opus-test"      # enable
    svc.llm = _SummaryLLM(out="S&P 500 rose <n> points today.")
    cc = CapturingComposer()
    agent = _agent(market=FakeMarket(indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
                   watchlist_factory=lambda: FakeWatchlist([]),
                   composer_factory=lambda: cc)
    agent.run_scheduled(svc)
    sent = svc.channel.sent[-1]["text"]
    assert "<b>Today's Summary</b>" in sent                      # our heading is intact
    assert "S&amp;P 500" in sent                                 # model's "&" escaped
    assert "S&P 500" not in sent                                 # bare "&" never reaches Telegram
    assert "&lt;n&gt;" in sent                                   # model's "<n>" escaped


def test_no_summary_when_model_unset():
    store = MemStore(); svc = _svc(store)                        # Cfg has no stock_summary_model -> ""
    svc.llm = _SummaryLLM()
    cc = CapturingComposer()
    agent = _agent(market=FakeMarket(indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
                   watchlist_factory=lambda: FakeWatchlist([]),
                   composer_factory=lambda: cc)
    agent.run_scheduled(svc)
    assert svc.llm.calls == []                                   # no summary call
    assert "Today's Summary" not in svc.channel.sent[-1]["text"]


def test_market_headlines_populated_in_personalized_mode():
    store = MemStore(); svc = _svc(store); cc = CapturingComposer()
    agent = _agent(
        market=FakeMarket(pct={"AAPL": 5.0},
                          indices={"^GSPC": {"level": 5050.0, "pct": 1.0}}),
        news=FakeNews(company={"AAPL": [DigestItem(source="finnhub", title="Apple thing",
                                                   url="https://x/aapl")]},
                      market=[DigestItem(source="finnhub", title="Fed holds rates",
                                         url="https://x/fed")]),
        watchlist_factory=lambda: FakeWatchlist(["AAPL"]),
        composer_factory=lambda: cc,
    )
    agent.run_scheduled(svc)
    assert cc.recap.movers                                   # personalized
    assert any(getattr(n, "url", "") == "https://x/fed" for n in cc.recap.market_news)
