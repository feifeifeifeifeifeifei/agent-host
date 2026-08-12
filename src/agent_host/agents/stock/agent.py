import html
from datetime import date, datetime
from zoneinfo import ZoneInfo

from agent_host.agents.base import Agent
from agent_host.agents.stock.calendar import is_trading_day as _is_trading_day
from agent_host.agents.stock.calendar import prior_trading_day as _prior_trading_day
from agent_host.agents.stock.composer import RecapData, StockComposer
from agent_host.agents.stock.image_import import ImageImporter
from agent_host.agents.stock.leveraged import catalyst_symbol
from agent_host.agents.stock.universe import load_universe
from agent_host.agents.stock.watchlist import WatchlistManager

INDEX_NAMES = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite", "^DJI": "Dow Jones",
    "^SOX": "PHLX Semiconductor", "^TNX": "US 10Y Yield",
}


def _today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


HELP = (
    "<b>StockAgent — daily US-market recap</b>\n"
    "Commands:\n"
    "/tickers — show your watchlist\n"
    "/add AAPL MSFT — add symbols (validated)\n"
    "/remove AAPL — remove symbols\n"
    "/reset — clear watchlist (back to tracking the market)\n"
    "/help — this message\n"
    "Screenshot import: send a photo of your brokerage / TradingView holdings; "
    "I'll show the detected tickers — then /confirm to add or /cancel to discard."
)


class StockAgent(Agent):
    name = "stock"
    schedule = "0 16 * * 1-5"          # 16:00 MON-FRI (tz + holiday gating in code)
    commands = ["/tickers", "/add", "/remove", "/reset", "/help", "/confirm", "/cancel"]
    intent = "Daily after-close US-market recap."

    def __init__(self, *, universe=None, market=None, news=None,
                 watchlist_factory=None, composer_factory=None,
                 is_trading_day=_is_trading_day, today_fn=_today_in):
        self._universe = universe
        self._market = market
        self._news = news
        self._watchlist_factory = watchlist_factory
        self._composer_factory = composer_factory
        self._is_trading_day = is_trading_day
        self._today_fn = today_fn

    def _get_universe(self, svc):
        if self._universe is not None:
            return self._universe
        # Phase 02 supplies the NASDAQ-file fetcher; until then callers inject a
        # Universe, or a cached blob must already exist in the store.
        return load_universe(svc.store)

    def _wm(self, svc):
        if self._watchlist_factory is not None:
            return self._watchlist_factory()
        chat_id = getattr(svc.config, "telegram_chat_id", "0")
        max_t = getattr(svc.config, "stock_max_tickers", 50)
        return WatchlistManager(svc.store, chat_id, self._get_universe(svc), max_tickers=max_t)

    def _resolve_sources(self, svc):
        market = self._market
        if market is None:
            from agent_host.agents.stock.sources.yfinance_source import YFinanceSource
            market = YFinanceSource(
                max_workers=getattr(svc.config, "stock_fetch_workers", 8))
        news = self._news
        if news is None:
            from agent_host.agents.stock.sources.finnhub_source import FinnhubSource
            news = FinnhubSource(
                getattr(svc.config, "finnhub_api_key", ""),
                lookback_days=getattr(svc.config, "stock_news_lookback_days", 2))
        return market, news

    # ------------------------------------------------------------------ routing
    def handle_message(self, msg, svc):
        if getattr(msg, "photo_file_ids", None):
            return self._handle_photo(msg, svc)
        text = (msg.text or "").strip()
        if not text.startswith("/"):
            return None   # command-only agent; free text belongs to ChatAgent
        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd == "/tickers":
            return self._cmd_tickers(svc)
        if cmd == "/add":
            return self._cmd_add(args, svc)
        if cmd == "/remove":
            return self._cmd_remove(args, svc)
        if cmd == "/reset":
            return self._cmd_reset(svc)
        if cmd == "/help":
            return HELP
        if cmd == "/confirm":
            return self._importer(svc).confirm()
        if cmd == "/cancel":
            return self._importer(svc).cancel()
        return "Unknown command. Try /help."

    def _importer(self, svc):
        return ImageImporter(svc.llm, self._wm(svc), self._get_universe(svc),
                             max_tickers=getattr(svc.config, "stock_max_tickers", 50))

    def _handle_photo(self, msg, svc):
        image_bytes = svc.channel.download_file(msg.photo_file_ids[0])
        return self._importer(svc).import_photo(image_bytes)

    # ------------------------------------------------------------------ commands
    def _cmd_tickers(self, svc):
        pool = self._wm(svc).get()
        if not pool:
            return ("<b>Watchlist empty.</b> Tracking the market by default. "
                    "Use /add to personalize.")
        listed = ", ".join(html.escape(s) for s in pool)
        return f"<b>Your watchlist ({len(pool)}):</b> {listed}"

    def _cmd_add(self, args, svc):
        if not args:
            return "Usage: /add AAPL MSFT NVDA"
        return _format_result(self._wm(svc).add(args))

    def _cmd_remove(self, args, svc):
        if not args:
            return "Usage: /remove AAPL MSFT"
        removed = self._wm(svc).remove(args)
        if not removed:
            return "None of those symbols were in your watchlist."
        return "<b>Removed:</b> " + ", ".join(html.escape(s) for s in removed)

    def _cmd_reset(self, svc):
        self._wm(svc).reset()
        return "<b>Watchlist cleared.</b> Tracking the market by default."

    # ------------------------------------------------------------------ recap helpers
    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - one dead source must not kill the recap
            return default

    def _gather_indices(self, market):
        levels = self._safe(market.index_levels, {})
        return [{"symbol": s, "name": INDEX_NAMES.get(s, s),
                 "level": d.get("level"), "pct": d.get("pct")}
                for s, d in levels.items()]

    def _select_movers(self, pct, config):
        threshold = getattr(config, "stock_mover_threshold_pct", 4.0)
        max_movers = getattr(config, "stock_max_movers", 5)
        notable = [(s, p) for s, p in pct.items()
                   if p is not None and abs(p) >= threshold]
        notable.sort(key=lambda sp: abs(sp[1]), reverse=True)
        return [{"symbol": s, "pct": p} for s, p in notable[:max_movers]]

    def _gather_earnings(self, market, pool, today):
        window = {today, _prior_trading_day(today)}
        cats = {s: catalyst_symbol(s) for s in pool}
        targets = list(dict.fromkeys(cats.values()))          # deduped catalyst symbols
        by_cat = self._safe(lambda: market.earnings_dates_bulk(targets), {})
        out = []
        for s in pool:
            cat = cats[s]
            if window & set(by_cat.get(cat, [])):
                note = "reported earnings" if cat == s else f"reported earnings (via {cat})"
                out.append({"symbol": s, "note": note})
        return out

    _ROUNDUP_MARKERS = (
        "top gainers", "top losers", "gainers and losers", "biggest movers",
        "stocks to watch", "stocks that defined", "things to know", "market wrap",
        "movers to watch", "stocks moving", "premarket movers", "after-hours movers",
    )

    @classmethod
    def _is_roundup(cls, title) -> bool:
        t = (title or "").lower()
        return any(marker in t for marker in cls._ROUNDUP_MARKERS)

    @classmethod
    def _pick_headline(cls, items):
        """First company-specific (non-roundup) headline, or None."""
        for it in items:
            if not cls._is_roundup(getattr(it, "title", "")):
                return it
        return None

    # ------------------------------------------------------------------ scheduled
    def run_scheduled(self, svc) -> None:
        tz = getattr(svc.config, "stock_schedule_tz", "America/Vancouver")
        today = self._today_fn(tz)
        if not self._is_trading_day(today):
            return  # holiday/weekend: send nothing, record nothing

        market, news = self._resolve_sources(svc)
        wl = self._wm(svc)
        pool = self._safe(wl.get, [])
        indices = self._gather_indices(market)

        earnings: list = []
        movers: list = []
        if pool:
            mode = "personalized"
            pct = self._safe(lambda: market.pct_changes(pool), {})
            movers = self._select_movers(pct, svc.config)
            earnings = self._gather_earnings(market, pool, today)
            earnings_syms = {e["symbol"] for e in earnings}
            for m in movers:
                sym = m["symbol"]
                m["catalyst"] = catalyst_symbol(sym)
                if sym in earnings_syms:
                    m["cause_kind"] = "earnings"
                    m["headline"] = None
                else:
                    items = self._safe(lambda c=m["catalyst"]: news.company_news(c), [])
                    hl = self._pick_headline(items)
                    m["cause_kind"] = "news" if hl is not None else "none"
                    m["headline"] = hl
        else:
            mode = "market"

        cap = getattr(svc.config, "stock_market_headlines", 8)
        mover_urls = {getattr(m.get("headline"), "url", None) for m in movers}
        mover_urls.discard(None)
        fetched = self._safe(news.market_news, [])
        market_news = [n for n in fetched
                       if getattr(n, "url", None) not in mover_urls][:cap]

        title = f"US Market Recap · {today:%b} {today.day}, {today.year}"
        recap = RecapData(title=title, indices=indices, movers=movers,
                          earnings=earnings, market_news=market_news,
                          personalized=bool(pool))
        if self._composer_factory is not None:
            composer = self._composer_factory()
        else:
            composer = StockComposer(getattr(svc.config, "output_language", "en"))
        html_text = composer.compose(recap)
        svc.channel.send(html_text)
        svc.store.record_run({"agent": "stock", "mode": mode, "chars": len(html_text)})


def _format_result(result):
    lines = []
    if result.accepted:
        lines.append("<b>Added:</b> " + ", ".join(html.escape(s) for s in result.accepted))
    if result.rejected:
        rej = "; ".join(
            f"{html.escape(sym)} ({html.escape(reason)})" for sym, reason in result.rejected
        )
        lines.append("<b>Rejected:</b> " + rej)
    return "\n".join(lines) if lines else "Nothing to add."
