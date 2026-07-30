from datetime import date

import holidays


def is_trading_day(d: date) -> bool:
    """True iff d is a NYSE (XNYS) session: a weekday that is not a market holiday.

    Uses the pure-Python `holidays` package (no numpy/pandas) to keep the Lambda
    package small. Early-close half-days are still treated as full sessions.
    """
    if d.weekday() >= 5:                       # 5=Sat, 6=Sun
        return False
    xnys = holidays.financial_holidays("XNYS", years=d.year)
    return d not in xnys
