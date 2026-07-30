# tests/test_stock_watchlist.py
from agent_host.agents.stock.watchlist import (
    sanitize_unicode,
    normalize_symbol,
    shape_ok,
    is_probable_crypto,
)


def test_sanitize_strips_zero_width_and_bidi():
    assert sanitize_unicode("A​APL") == "AAPL"        # zero-width space removed
    assert sanitize_unicode("AAPL‮") == "AAPL"        # bidi override removed
    assert sanitize_unicode("AAPL﻿") == "AAPL"        # BOM/word-joiner removed


def test_sanitize_strips_unicode_tag_chars():
    hidden = "AAPL" + "".join(chr(cp) for cp in range(0xE0069, 0xE0069 + 3))
    assert sanitize_unicode(hidden) == "AAPL"


def test_sanitize_nfkc_folds_fullwidth():
    assert sanitize_unicode("ＡＡＰＬ") == "AAPL"   # fullwidth Latin -> ASCII


def test_normalize_upper_and_trim():
    assert normalize_symbol("  aapl  ") == "AAPL"
    assert normalize_symbol("brk.b") == "BRK.B"


def test_shape_ok_admits_supported_forms():
    for good in ["AAPL", "MSFT", "BRK.B", "^GSPC", "^TNX", "CL=F", "GC=F", "GOOGL"]:
        assert shape_ok(good), good


def test_shape_ok_rejects_garbage():
    for bad in ["$$$$", "123", "XX.YY", "A B", "META!", "=IMPORTXML", "<img>", ""]:
        assert not shape_ok(bad), bad


def test_is_probable_crypto():
    for c in ["BTC-USD", "ETH-USD", "DOGE-USD", "BTC", "ETHUSD", "BTC/USD", "sol-usd"]:
        assert is_probable_crypto(c), c


def test_non_crypto_not_flagged():
    for ok in ["AAPL", "MSFT", "^GSPC", "CL=F", "BRK.B"]:
        assert not is_probable_crypto(ok), ok
