# tests/test_stock_agent.py
from types import SimpleNamespace
from agent_host.agents.stock.agent import StockAgent
from agent_host.agents.stock.universe import Universe
from agent_host.models import InboundMessage

_NASDAQ = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc.|Q|N|N|100|N|N\n"
    "MSFT|Microsoft Corp|Q|N|N|100|N|N\n"
    "NVDA|NVIDIA Corp|Q|N|N|100|N|N\n"
    "File Creation Time: 07292026 18:00|||||||\n"
)
_OTHER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
    "File Creation Time: 07292026 18:00||||||||\n"
)


class FakeStore:
    def __init__(self):
        self._p = {}

    def get_prefs(self, cid):
        return dict(self._p.get(cid, {}))

    def set_prefs(self, cid, prefs):
        self._p[cid] = dict(prefs)


def _svc(store):
    config = SimpleNamespace(telegram_chat_id="42", stock_max_tickers=50)
    return SimpleNamespace(store=store, config=config)


def _agent():
    return StockAgent(universe=Universe.from_nasdaq_files(_NASDAQ, _OTHER))


def _msg(text):
    return InboundMessage(chat_id="42", text=text)


def test_identity_and_commands():
    a = _agent()
    assert a.name == "stock"
    assert a.commands == ["/tickers", "/add", "/remove", "/reset", "/help", "/confirm", "/cancel"]


def test_tickers_empty_mentions_default_mode():
    reply = _agent().handle_message(_msg("/tickers"), _svc(FakeStore()))
    assert "empty" in reply.lower()
    assert "market" in reply.lower()


def test_add_valid_applies_immediately():
    store = FakeStore()
    agent = _agent()
    reply = agent.handle_message(_msg("/add aapl MSFT ZZZZ"), _svc(store))
    assert "AAPL" in reply and "MSFT" in reply
    assert "ZZZZ" in reply                       # reported as rejected
    # persisted
    reply2 = agent.handle_message(_msg("/tickers"), _svc(store))
    assert "AAPL" in reply2 and "MSFT" in reply2


def test_add_crypto_reports_crypto_reason():
    reply = _agent().handle_message(_msg("/add BTC-USD"), _svc(FakeStore()))
    assert "crypto not supported" in reply


def test_add_without_args_shows_usage():
    reply = _agent().handle_message(_msg("/add"), _svc(FakeStore()))
    assert "usage" in reply.lower()


def test_remove_and_reset():
    store = FakeStore()
    agent = _agent()
    agent.handle_message(_msg("/add AAPL NVDA"), _svc(store))
    r_remove = agent.handle_message(_msg("/remove nvda"), _svc(store))
    assert "NVDA" in r_remove
    r_reset = agent.handle_message(_msg("/reset"), _svc(store))
    assert "cleared" in r_reset.lower()
    assert "empty" in agent.handle_message(_msg("/tickers"), _svc(store)).lower()


def test_help_lists_commands():
    reply = _agent().handle_message(_msg("/help"), _svc(FakeStore()))
    assert "/add" in reply and "/tickers" in reply


def test_confirm_cancel_are_stubs():
    agent = _agent()
    assert "nothing" in agent.handle_message(_msg("/confirm"), _svc(FakeStore())).lower()
    assert "nothing" in agent.handle_message(_msg("/cancel"), _svc(FakeStore())).lower()


def test_free_text_not_handled():
    assert _agent().handle_message(_msg("hello there"), _svc(FakeStore())) is None


def test_run_scheduled_is_noop_this_phase():
    assert _agent().run_scheduled(_svc(FakeStore())) is None
