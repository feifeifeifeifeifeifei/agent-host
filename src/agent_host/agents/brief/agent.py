import hashlib
from agent_host.agents.base import Agent
from agent_host.agents.brief.sources.placeholder import PlaceholderSource
from agent_host.agents.brief.composer import Composer


def _key(item) -> str:
    basis = item.url or item.title
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class BriefAgent(Agent):
    name = "brief"
    schedule = "0 8 * * *"          # 08:00 daily (timezone from Config/EventBridge)
    commands = ["/brief"]
    intent = "Produce the daily news brief."

    def __init__(self, sources=None):
        self._sources = sources if sources is not None else [PlaceholderSource()]

    def _gather(self, svc):
        items = []
        for src in self._sources:
            try:
                items.extend(src.fetch())
            except Exception:  # noqa: BLE001 - one dead source must not kill the brief
                continue
        return items

    def _build(self, svc, dedup: bool) -> str:
        items = self._gather(svc)
        if dedup:
            fresh = [i for i in items if not svc.store.seen(_key(i))]
            svc.store.mark_seen([_key(i) for i in fresh])
        else:
            fresh = items
        composer = Composer(svc.llm, getattr(svc.config, "output_language", "zh"))
        return composer.compose(fresh, svc.store.get_prefs(svc.config.telegram_chat_id)
                                if hasattr(svc.config, "telegram_chat_id") else {})

    def run_scheduled(self, svc) -> None:
        html = self._build(svc, dedup=True)
        svc.channel.send(html)
        svc.store.record_run({"agent": "brief", "chars": len(html)})

    def handle_message(self, msg, svc) -> str | None:
        return self._build(svc, dedup=False)
