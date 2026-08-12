from datetime import date

from agent_host.agents.stock.calendar import is_trading_day, prior_trading_day


def test_weekday_normal_day_is_trading():
    assert is_trading_day(date(2026, 7, 29)) is True     # Wednesday, normal


def test_weekend_is_not_trading():
    assert is_trading_day(date(2026, 8, 1)) is False      # Saturday


def test_market_holiday_is_not_trading():
    assert is_trading_day(date(2025, 12, 25)) is False    # Christmas (XNYS)
    assert is_trading_day(date(2026, 1, 1)) is False       # New Year's Day (XNYS)


def test_prior_trading_day_skips_weekend():
    # Monday 2026-08-10 -> prior session Friday 2026-08-07
    assert prior_trading_day(date(2026, 8, 10)) == date(2026, 8, 7)


def test_prior_trading_day_midweek():
    assert prior_trading_day(date(2026, 8, 6)) == date(2026, 8, 5)


def test_prior_trading_day_skips_holiday():
    # Day after US Independence Day observed etc. — walk back over a holiday.
    # 2026-01-20 is the Tuesday after MLK Day (Mon 2026-01-19, XNYS holiday);
    # prior session is Friday 2026-01-16.
    assert prior_trading_day(date(2026, 1, 20)) == date(2026, 1, 16)
