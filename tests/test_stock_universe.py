from agent_host.agents.stock import universe as universe_module
from agent_host.agents.stock.universe import Universe, load_universe

NASDAQ = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
    "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N\n"
    "MSFT|Microsoft Corp - Common Stock|Q|N|N|100|N|N\n"
    "NVDA|NVIDIA Corp - Common Stock|Q|N|N|100|N|N\n"
    "TSLA|Tesla Inc - Common Stock|Q|N|N|100|N|N\n"
    "META|Meta Platforms Inc - Common Stock|Q|N|N|100|N|N\n"
    "ZWZZT|NASDAQ TEST STOCK|Q|Y|N|100|N|N\n"
    "File Creation Time: 07292026 18:00|||||||\n"
)

OTHER = (
    "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
    "BRK.B|Berkshire Hathaway Inc Class B|N|BRK.B|N|100|N|BRK.B\n"
    "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
    "SOXX|iShares Semiconductor ETF|Z|SOXX|Y|100|N|SOXX\n"
    "XXTEST|NYSE TEST ISSUE|N|XXTEST|N|100|Y|XXTEST\n"
    "File Creation Time: 07292026 18:00||||||||\n"
)


class FakeStore:
    def __init__(self):
        self._p = {}

    def get_prefs(self, cid):
        return dict(self._p.get(cid, {}))   # copy: caller must not alias cache

    def set_prefs(self, cid, prefs):
        self._p[cid] = dict(prefs)


def _uni():
    return Universe.from_nasdaq_files(NASDAQ, OTHER)


def test_parses_valid_symbols():
    u = _uni()
    assert u.is_listed("AAPL")
    assert u.is_listed("MSFT")
    assert u.is_listed("BRK.B")
    assert u.is_listed("SPY")


def test_drops_test_issues():
    u = _uni()
    assert not u.is_listed("ZWZZT")   # Test Issue == Y in nasdaqlisted
    assert not u.is_listed("XXTEST")  # Test Issue == Y in otherlisted


def test_strips_footer_and_header():
    u = _uni()
    assert not u.is_listed("File")
    assert not u.is_listed("Symbol")
    assert not u.is_listed("ACT Symbol")


def test_symbol_types():
    u = _uni()
    assert u.symbol_type("AAPL") == "equity"
    assert u.symbol_type("BRK.B") == "equity"
    assert u.symbol_type("SPY") == "etf"
    assert u.symbol_type("SOXX") == "etf"


def test_curated_non_equity_merged():
    u = _uni()
    assert u.is_listed("^GSPC") and u.symbol_type("^GSPC") == "index"
    assert u.is_listed("^SOX") and u.symbol_type("^SOX") == "index"
    assert u.is_listed("^TNX") and u.symbol_type("^TNX") == "index"
    assert u.is_listed("CL=F") and u.symbol_type("CL=F") == "future"
    assert u.is_listed("GC=F") and u.symbol_type("GC=F") == "future"


def test_no_crypto_anywhere():
    u = _uni()
    assert not u.is_listed("BTC-USD")
    assert not u.is_listed("ETH-USD")
    assert u.symbol_type("BTC-USD") is None
    # No curated symbol contains a crypto marker.
    assert all("-USD" not in s for s in u.symbols)


def test_unknown_symbol_type_is_none():
    u = _uni()
    assert u.symbol_type("ZZZZ") is None


def test_load_universe_caches_after_first_fetch():
    store = FakeStore()
    calls = []

    def fetch():
        calls.append(1)
        return NASDAQ, OTHER

    u1 = load_universe(store, fetch=fetch)
    assert u1.is_listed("AAPL")
    assert len(calls) == 1
    u2 = load_universe(store, fetch=fetch)   # served from cache, no re-fetch
    assert len(calls) == 1
    assert u2.is_listed("AAPL")


def test_load_universe_refreshes_when_stale():
    store = FakeStore()
    load_universe(store, fetch=lambda: (NASDAQ, OTHER))
    blob = store.get_prefs("__universe__")
    blob["fetched_at"] = "2000-01-01T00:00:00+00:00"   # force stale
    store.set_prefs("__universe__", blob)
    calls = []

    def fetch2():
        calls.append(1)
        return NASDAQ, OTHER

    load_universe(store, ttl_days=7, fetch=fetch2)
    assert len(calls) == 1   # stale cache triggered a refresh


def test_default_fetch_hits_both_nasdaq_trader_urls(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, text):
            self.text = text

    def fake_get(url, timeout=15):
        calls.append(url)
        if "nasdaqlisted.txt" in url:
            return FakeResponse(NASDAQ)
        if "otherlisted.txt" in url:
            return FakeResponse(OTHER)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(universe_module.httpx, "get", fake_get)
    nasdaq_text, other_text = universe_module._default_fetch()
    assert nasdaq_text == NASDAQ
    assert other_text == OTHER
    assert any("nasdaqlisted.txt" in u for u in calls)
    assert any("otherlisted.txt" in u for u in calls)
    assert len(calls) == 2   # no real network call made


def test_load_universe_with_no_fetch_uses_default_fetch(monkeypatch):
    store = FakeStore()
    calls = []

    def fake_default_fetch():
        calls.append(1)
        return NASDAQ, OTHER

    monkeypatch.setattr(universe_module, "_default_fetch", fake_default_fetch)
    u = load_universe(store)   # no explicit fetch -> falls back to _default_fetch
    assert u.is_listed("AAPL")
    assert len(calls) == 1
    blob = store.get_prefs("__universe__")
    assert blob.get("fetched_at")   # cached for next call


def test_load_universe_falls_back_to_stale_cache_on_fetch_failure():
    store = FakeStore()
    load_universe(store, fetch=lambda: (NASDAQ, OTHER))        # 先填缓存
    blob = store.get_prefs("__universe__")
    blob["fetched_at"] = "2000-01-01T00:00:00+00:00"           # 强制过期
    store.set_prefs("__universe__", blob)

    def boom_fetch():
        raise RuntimeError("nasdaqtrader down")

    u = load_universe(store, ttl_days=7, fetch=boom_fetch)     # 过期 + fetch 失败
    assert u.is_listed("AAPL")                                 # 回退到旧缓存,不抛


def test_load_universe_raises_when_fetch_fails_and_no_cache():
    store = FakeStore()

    def boom_fetch():
        raise RuntimeError("nasdaqtrader down")

    import pytest
    with pytest.raises(RuntimeError):
        load_universe(store, fetch=boom_fetch)                 # 无缓存可回退 → 抛


def test_default_fetch_retries_transient_failures(monkeypatch):
    attempts = []

    class FakeResponse:
        def __init__(self, text): self.text = text

    def flaky_get(url, timeout=15):
        attempts.append(url)
        if len(attempts) < 3:                                  # 前两次失败
            raise RuntimeError("transient")
        return FakeResponse(NASDAQ if "nasdaqlisted" in url else OTHER)

    monkeypatch.setattr(universe_module, "_RETRY_SLEEP", lambda _n: None)  # 别真 sleep
    monkeypatch.setattr(universe_module.httpx, "get", flaky_get)
    nasdaq_text, other_text = universe_module._default_fetch()
    assert nasdaq_text == NASDAQ
    assert len(attempts) >= 3                                  # 确有重试
