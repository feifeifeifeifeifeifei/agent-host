from __future__ import annotations

import re
import unicodedata

# --- rejection reasons (stable strings; Tasks 4-5 and the agent surface these) ---
REASON_CRYPTO = "crypto not supported"
REASON_SHAPE = "invalid symbol format"
REASON_UNKNOWN = "unknown or delisted symbol"
REASON_EMPTY = "empty or unrecognized input"


def _reason_cap(n: int) -> str:
    return f"exceeds max {n} tickers"


# Zero-width / joiner / BOM code points.
_ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
# Bidirectional formatting controls (LRE..PDI).
_BIDI = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}


def sanitize_unicode(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        cp = ord(ch)
        if 0xE0000 <= cp <= 0xE007F:       # Unicode tag chars (invisible smuggling)
            continue
        if cp in _ZERO_WIDTH or cp in _BIDI:
            continue
        out.append(ch)
    return "".join(out)


def normalize_symbol(s: str) -> str:
    return sanitize_unicode(s).strip().upper()


# Cheap pre-filter only (the allowlist is the real gate). Admits equities (AAPL),
# class shares (BRK.B), indices (^GSPC/^TNX), and futures (CL=F).
_SHAPE = re.compile(r"^(\^[A-Z]{1,6}|[A-Z]{1,6}(\.[A-Z])?|[A-Z]{1,5}=F)$")


def shape_ok(s: str) -> bool:
    return bool(_SHAPE.match(s))


_CRYPTO_BASES = frozenset({
    "BTC", "ETH", "DOGE", "SOL", "XRP", "ADA", "BNB", "LTC", "DOT", "AVAX",
    "MATIC", "SHIB", "TRX", "LINK", "BCH", "XLM", "USDT", "USDC",
})
_CRYPTO_PAIR = re.compile(r"^([A-Z]{2,6})[-/]?(USD|USDT|USDC)$")


def is_probable_crypto(s: str) -> bool:
    t = normalize_symbol(s)
    if t in _CRYPTO_BASES:
        return True
    m = _CRYPTO_PAIR.match(t)
    return bool(m and m.group(1) in _CRYPTO_BASES)


from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    accepted: list = field(default_factory=list)   # list[str]
    rejected: list = field(default_factory=list)    # list[tuple[str, str]]


def validate_candidates(raw, universe, *, max_tickers: int) -> ValidationResult:
    """Deterministic gate: normalize -> crypto-reason -> shape -> allowlist ->
    silent dedupe -> count cap. This is the ONLY authority for pool membership;
    nothing an LLM emits is trusted past this function."""
    result = ValidationResult()
    seen: set[str] = set()
    for original in raw:
        text = original if isinstance(original, str) else str(original)
        sym = normalize_symbol(text)
        if not sym:
            result.rejected.append((text, REASON_EMPTY))
            continue
        if is_probable_crypto(sym):
            result.rejected.append((sym, REASON_CRYPTO))
            continue
        if not shape_ok(sym):
            result.rejected.append((sym, REASON_SHAPE))
            continue
        if not universe.is_listed(sym):
            result.rejected.append((sym, REASON_UNKNOWN))
            continue
        if sym in seen:
            continue   # silent dedupe (not a rejection)
        if len(result.accepted) >= max_tickers:
            result.rejected.append((sym, _reason_cap(max_tickers)))
            continue
        seen.add(sym)
        result.accepted.append(sym)
    return result


from datetime import datetime, timezone


class WatchlistManager:
    def __init__(self, store, chat_id, universe, *, max_tickers):
        self._store = store
        self._chat_id = str(chat_id)
        self._universe = universe
        self._max = max_tickers

    def _prefs(self) -> dict:
        return self._store.get_prefs(self._chat_id)

    def _save(self, prefs) -> None:
        self._store.set_prefs(self._chat_id, prefs)

    def get(self) -> list:
        return list(self._prefs().get("watchlist", {}).get("tickers", []))

    def _set_tickers(self, tickers) -> None:
        prefs = self._prefs()
        prefs["watchlist"] = {
            "tickers": tickers,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(prefs)

    def add(self, symbols) -> ValidationResult:
        existing = self.get()
        result = validate_candidates(symbols, self._universe, max_tickers=self._max)
        added = []
        for sym in result.accepted:
            if sym in existing or sym in added:
                continue
            if len(existing) + len(added) >= self._max:
                result.rejected.append((sym, _reason_cap(self._max)))
                continue
            added.append(sym)
        if added:
            self._set_tickers(existing + added)
        return ValidationResult(accepted=added, rejected=result.rejected)

    def remove(self, symbols) -> list:
        existing = self.get()
        targets = {normalize_symbol(s) for s in symbols}
        removed = [s for s in existing if s in targets]
        if removed:
            self._set_tickers([s for s in existing if s not in targets])
        return removed

    def reset(self) -> None:
        self._set_tickers([])

    def set_pending(self, cands) -> None:
        prefs = self._prefs()
        prefs["pending_import"] = {
            "candidates": list(cands),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(prefs)

    def get_pending(self) -> list:
        return list(self._prefs().get("pending_import", {}).get("candidates", []))

    def clear_pending(self) -> None:
        prefs = self._prefs()
        prefs.pop("pending_import", None)
        self._save(prefs)
