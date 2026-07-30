from datetime import date

from agent_host.agents.stock.calendar import is_trading_day


def test_weekday_normal_day_is_trading():
    assert is_trading_day(date(2026, 7, 29)) is True     # Wednesday, normal


def test_weekend_is_not_trading():
    assert is_trading_day(date(2026, 8, 1)) is False      # Saturday


def test_market_holiday_is_not_trading():
    assert is_trading_day(date(2025, 12, 25)) is False    # Christmas (XNYS)
    assert is_trading_day(date(2026, 1, 1)) is False       # New Year's Day (XNYS)
