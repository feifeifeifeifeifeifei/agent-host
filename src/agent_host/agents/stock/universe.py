from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

log = logging.getLogger(__name__)

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

# Curated non-equity symbols supported by StockAgent. NO CRYPTO is ever added
# here — crypto pairs (BTC-USD, ETH-USD, ...) are rejected exactly like any
# other non-member of the allowlist.
CURATED_INDICES = frozenset({"^GSPC", "^IXIC", "^DJI", "^SOX", "^TNX"})
CURATED_FUTURES = frozenset({"CL=F", "GC=F", "SI=F", "ZS=F"})

_UNIVERSE_KEY = "__universe__"


@dataclass(frozen=True)
class Universe:
    symbols: frozenset
    types: dict  # symbol -> "equity" | "etf" | "index" | "future"

    def is_listed(self, sym: str) -> bool:
        return sym in self.symbols

    def symbol_type(self, sym: str) -> str | None:
        return self.types.get(sym)

    @classmethod
    def from_nasdaq_files(cls, nasdaq_listed_text: str, other_listed_text: str) -> "Universe":
        types: dict[str, str] = {}
        _parse(nasdaq_listed_text, "Symbol", types)
        _parse(other_listed_text, "ACT Symbol", types)
        for s in CURATED_INDICES:
            types[s] = "index"
        for s in CURATED_FUTURES:
            types[s] = "future"
        return cls(symbols=frozenset(types), types=types)

    def to_blob(self) -> dict:
        return {"types": dict(self.types)}

    @classmethod
    def from_blob(cls, blob: dict) -> "Universe":
        types = dict(blob.get("types", {}))
        return cls(symbols=frozenset(types), types=types)


def _parse(text: str, symbol_col: str, types: dict) -> None:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return
    header = [h.strip() for h in lines[0].split("|")]
    try:
        col = {name: i for i, name in enumerate(header)}
        sym_i = col[symbol_col]
        test_i = col["Test Issue"]
        etf_i = col["ETF"]
    except KeyError:
        return
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):   # footer
            continue
        parts = ln.split("|")
        if len(parts) <= max(sym_i, test_i, etf_i):
            continue
        sym = parts[sym_i].strip()
        if not sym or parts[test_i].strip() == "Y":
            continue
        types[sym] = "etf" if parts[etf_i].strip() == "Y" else "equity"


def _RETRY_SLEEP(attempt: int) -> None:          # 可被测试 monkeypatch 掉
    time.sleep(2 ** attempt)


def _default_fetch(retries: int = 3) -> tuple[str, str]:
    """Production fetcher: pulls the two NASDAQ Trader symbol directory files,
    retrying transient failures with exponential backoff."""
    last_exc = None
    for attempt in range(retries):
        try:
            nasdaq_text = httpx.get(NASDAQ_LISTED_URL, timeout=15).text
            other_text = httpx.get(OTHER_LISTED_URL, timeout=15).text
            return nasdaq_text, other_text
        except Exception as exc:  # noqa: BLE001 - retry any transient fetch error
            last_exc = exc
            if attempt < retries - 1:
                _RETRY_SLEEP(attempt)
    raise last_exc


def load_universe(store, *, ttl_days: int = 7, fetch=None) -> Universe:
    blob = store.get_prefs(_UNIVERSE_KEY)
    if blob and _fresh(blob.get("fetched_at"), ttl_days):
        return Universe.from_blob(blob)
    if fetch is None:
        fetch = _default_fetch
    try:
        nasdaq_text, other_text = fetch()
    except Exception:  # noqa: BLE001 - fall back to stale cache if we have one
        if blob:
            log.warning("universe fetch failed; using stale cache")
            return Universe.from_blob(blob)
        raise
    uni = Universe.from_nasdaq_files(nasdaq_text, other_text)
    out = uni.to_blob()
    out["fetched_at"] = datetime.now(timezone.utc).isoformat()
    store.set_prefs(_UNIVERSE_KEY, out)
    return uni


def _fresh(fetched_at, ttl_days: int) -> bool:
    if not fetched_at:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - ts).days < ttl_days
