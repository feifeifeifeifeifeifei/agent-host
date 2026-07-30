import html

from agent_host.agents.base import Agent
from agent_host.agents.stock.universe import load_universe
from agent_host.agents.stock.watchlist import WatchlistManager

HELP = (
    "<b>StockAgent — daily US-market recap</b>\n"
    "Commands:\n"
    "/tickers — show your watchlist\n"
    "/add AAPL MSFT — add symbols (validated)\n"
    "/remove AAPL — remove symbols\n"
    "/reset — clear watchlist (back to tracking the market)\n"
    "/help — this message\n"
    "Screenshot import (brokerage / TradingView) is coming soon: send a photo, "
    "review the detected tickers, then /confirm."
)


class StockAgent(Agent):
    name = "stock"
    # The real 4pm-PT MON-FRI EventBridge schedule is wired in a later phase;
    # the skeleton is command-only.
    schedule = None
    commands = ["/tickers", "/add", "/remove", "/reset", "/help", "/confirm", "/cancel"]
    intent = "US market daily recap and watchlist management."

    def __init__(self, universe=None):
        self._universe = universe

    def _get_universe(self, svc):
        if self._universe is not None:
            return self._universe
        # Phase 02 supplies the NASDAQ-file fetcher; until then callers inject a
        # Universe, or a cached blob must already exist in the store.
        return load_universe(svc.store)

    def _wm(self, svc):
        chat_id = getattr(svc.config, "telegram_chat_id", "0")
        max_t = getattr(svc.config, "stock_max_tickers", 50)
        return WatchlistManager(svc.store, chat_id, self._get_universe(svc), max_tickers=max_t)

    # ------------------------------------------------------------------ routing
    def handle_message(self, msg, svc):
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
        if cmd in ("/confirm", "/cancel"):
            return self._cmd_pending_stub(cmd)
        return "Unknown command. Try /help."

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

    def _cmd_pending_stub(self, cmd):
        # Phase 03 wires image-import confirmation to /confirm and /cancel.
        if cmd == "/confirm":
            return "Nothing pending to confirm."
        return "Nothing pending to cancel."

    # ------------------------------------------------------------------ scheduled
    def run_scheduled(self, svc):
        # Phase 02 replaces this no-op with the real after-close recap pipeline
        # (it MODIFIES this file — keep handle_message + the /add etc. helpers).
        # Skeleton is a no-op.
        return None


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
