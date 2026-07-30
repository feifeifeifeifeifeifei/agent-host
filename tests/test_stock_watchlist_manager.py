from agent_host.agents.stock.watchlist import WatchlistManager
from agent_host.agents.stock.universe import Universe

_NASDAQ = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc.|Q|N|N|100|N|N\n"
    "MSFT|Microsoft Corp|Q|N|N|100|N|N\n"
    "NVDA|NVIDIA Corp|Q|N|N|100|N|N\n"
    "TSLA|Tesla Inc|Q|N|N|100|N|N\n"
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


def _wm(store, max_tickers=50):
    uni = Universe.from_nasdaq_files(_NASDAQ, _OTHER)
    return WatchlistManager(store, "42", uni, max_tickers=max_tickers)


def test_starts_empty():
    assert _wm(FakeStore()).get() == []


def test_add_valid_persists_and_reports():
    store = FakeStore()
    wm = _wm(store)
    r = wm.add(["aapl", "MSFT", "ZZZZ"])
    assert r.accepted == ["AAPL", "MSFT"]
    assert ("ZZZZ", "unknown or delisted symbol") in r.rejected
    assert wm.get() == ["AAPL", "MSFT"]
    # persisted so a fresh manager sees it
    assert _wm(store).get() == ["AAPL", "MSFT"]


def test_add_dedupes_against_existing():
    store = FakeStore()
    wm = _wm(store)
    wm.add(["AAPL"])
    r = wm.add(["AAPL", "NVDA"])
    assert r.accepted == ["NVDA"]          # AAPL already present, not re-added
    assert wm.get() == ["AAPL", "NVDA"]


def test_add_enforces_total_cap():
    store = FakeStore()
    wm = _wm(store, max_tickers=2)
    wm.add(["AAPL", "MSFT"])
    r = wm.add(["NVDA"])
    assert r.accepted == []
    assert any("exceeds max 2" in reason for _, reason in r.rejected)
    assert wm.get() == ["AAPL", "MSFT"]


def test_remove_returns_removed():
    store = FakeStore()
    wm = _wm(store)
    wm.add(["AAPL", "MSFT", "NVDA"])
    removed = wm.remove(["msft", "ZZZZ"])
    assert removed == ["MSFT"]             # only the one actually present
    assert wm.get() == ["AAPL", "NVDA"]


def test_reset_clears():
    store = FakeStore()
    wm = _wm(store)
    wm.add(["AAPL"])
    wm.reset()
    assert wm.get() == []


def test_pending_roundtrip():
    store = FakeStore()
    wm = _wm(store)
    assert wm.get_pending() == []
    wm.set_pending(["AAPL", "TSLA"])
    assert wm.get_pending() == ["AAPL", "TSLA"]
    wm.clear_pending()
    assert wm.get_pending() == []
