import re
from agent_host.agents.stock.leveraged import (
    LEVERAGED_UNDERLYING, catalyst_symbol, is_leveraged,
)


def test_catalyst_symbol_maps_leveraged_to_underlying():
    assert catalyst_symbol("PLTU") == "PLTR"
    assert catalyst_symbol("TSLL") == "TSLA"


def test_catalyst_symbol_passthrough_for_unmapped():
    assert catalyst_symbol("AAPL") == "AAPL"     # plain equity
    assert catalyst_symbol("TQQQ") == "TQQQ"     # index leveraged ETF: no single underlying


def test_is_leveraged():
    assert is_leveraged("PLTU") is True
    assert is_leveraged("AAPL") is False


def test_map_entries_are_wellformed():
    for k, v in LEVERAGED_UNDERLYING.items():
        assert re.fullmatch(r"[A-Z]{1,6}", k), k
        assert re.fullmatch(r"[A-Z]{1,6}", v), v
        assert k != v                            # a map entry must actually redirect
