from abc import ABC, abstractmethod
from datetime import date

from agent_host.models import DigestItem


class MarketDataSource(ABC):
    """Numeric/price side of the free data stack (yfinance-backed by default)."""

    @abstractmethod
    def pct_changes(self, symbols: list[str]) -> dict[str, float]:
        """Map each symbol to its latest day %-change; omit symbols with no data."""

    @abstractmethod
    def index_levels(self) -> dict[str, dict]:
        """Map index symbol -> {"level": float|None, "pct": float|None}."""

    @abstractmethod
    def sector(self, symbol: str) -> str | None:
        """Best-effort GICS sector; None when unavailable."""

    @abstractmethod
    def earnings_dates(self, symbol: str) -> list[date]:
        """Best-effort forward/known earnings dates; [] when unavailable."""

    def earnings_dates_bulk(self, symbols: list[str]) -> dict[str, list[date]]:
        """Map symbols -> earnings dates. Default loops earnings_dates; concrete
        sources may override with a batched/concurrent implementation."""
        return {s: self.earnings_dates(s) for s in symbols}


class NewsSource(ABC):
    """Headline/link side of the free data stack (Finnhub-backed by default)."""

    @abstractmethod
    def company_news(self, symbol: str) -> list[DigestItem]:
        """Recent company news with url + summary; [] when disabled/unavailable."""

    @abstractmethod
    def peers(self, symbol: str) -> list[str]:
        """Industry peers for propagation; [] when disabled/unavailable."""

    @abstractmethod
    def market_news(self) -> list[DigestItem]:
        """General market news; [] when disabled/unavailable."""

    @abstractmethod
    def earnings_surprises(self, symbol: str) -> list[dict]:
        """Past earnings surprise rows; [] when disabled/unavailable."""
