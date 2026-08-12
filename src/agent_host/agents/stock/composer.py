import html
from dataclasses import dataclass, field

_NO_DATA = "<b>No market data available today.</b>"
_INDICES_UNAVAILABLE = "Indices: unavailable today"


@dataclass
class RecapData:
    title: str                          # e.g. "US Market Recap · Aug 6, 2026" (plain text)
    indices: list                       # [{"symbol","name","level","pct"}]
    movers: list                        # [{"symbol","pct","catalyst","cause_kind","headline"}]
    earnings: list                      # [{"symbol","note"}]
    market_news: list = field(default_factory=list)   # [DigestItem]
    personalized: bool = True           # True => show Movers/Earnings


def _link(item) -> str:
    title = html.escape(getattr(item, "title", "") or "")
    url = getattr(item, "url", None)
    return f'<a href="{html.escape(url)}">{title}</a>' if url else title


class StockComposer:
    def __init__(self, language: str = "en"):
        self._lang = language           # English-only today; kept for future

    def _mover_line(self, m) -> str:
        sym = html.escape(str(m.get("symbol")))
        pct = m.get("pct")
        pct_s = f"{pct:+.2f}%" if pct is not None else "n/a"
        cat = m.get("catalyst") or m.get("symbol")
        lev = cat != m.get("symbol")
        kind = m.get("cause_kind")
        if kind == "earnings":
            cause = (f"reported earnings (via {html.escape(str(cat))})" if lev
                     else "reported earnings")
        elif kind == "news" and m.get("headline") is not None:
            prefix = (f"recent {html.escape(str(cat))} headline: " if lev
                      else "recent headline: ")
            cause = prefix + _link(m["headline"])
        else:
            cause = "no clear catalyst (likely sector/technical)"
        return f"<b>{sym}</b> {pct_s} — {cause}"

    def compose(self, recap: RecapData) -> str:
        # Indices content is computed separately from the other sections so that
        # "no indices data AND nothing else to show" can collapse to _NO_DATA,
        # while still letting the "unavailable" placeholder stand on its own
        # whenever some other section (e.g. an empty personalized Movers list,
        # which still renders "No movers...") has real content.
        indices_lines = None
        if recap.indices:
            lines = []
            for i in recap.indices:
                name = html.escape(str(i.get("name") or i.get("symbol")))
                sym = html.escape(str(i.get("symbol")))
                pct = i.get("pct")
                level = i.get("level")
                pct_s = f"{pct:+.2f}%" if pct is not None else "n/a"
                lvl_s = f"{level:.2f}" if level is not None else "n/a"
                lines.append(f"{name} ({sym}): {pct_s} to {lvl_s}")
            indices_lines = "\n".join(lines)

        other_sections: list[str] = []

        # Movers + Earnings — personalized mode only
        if recap.personalized:
            if recap.movers:
                mlines = [self._mover_line(m) for m in recap.movers]
            else:
                mlines = ["No movers beyond ±4% today."]
            other_sections.append("<b>Notable Movers</b>\n" + "\n".join(mlines))

            if recap.earnings:
                elines = [f"{html.escape(str(e.get('symbol', '')))} — "
                          f"{html.escape(str(e.get('note', '')))}" for e in recap.earnings]
                other_sections.append("<b>Earnings</b>\n" + "\n".join(elines))

        # Market Headlines — both modes
        if recap.market_news:
            hlines = [f"• {_link(n)}" for n in recap.market_news]
            other_sections.append("<b>Market Headlines</b>\n" + "\n".join(hlines))

        if indices_lines is None and not other_sections:
            return _NO_DATA

        # Indices — always present (numbers or an honest "unavailable")
        indices_section = "<b>Indices</b>\n" + (indices_lines if indices_lines is not None
                                                 else _INDICES_UNAVAILABLE)
        sections = [indices_section] + other_sections
        return f"<b>{html.escape(recap.title)}</b>\n\n" + "\n\n".join(sections)
