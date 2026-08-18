import html
import logging
from dataclasses import replace

from agent_host.agents.base import Agent
from agent_host.services import Services

log = logging.getLogger(__name__)


class Host:
    def __init__(self, agents: list[Agent], services: Services, default_agent: str):
        self._agents = {a.name: a for a in agents}
        self._services = services
        self._default = default_agent
        self._commands: dict[str, Agent] = {}
        for a in agents:
            for cmd in a.commands:
                if cmd in self._commands:
                    raise ValueError(
                        f"command {cmd!r} is owned by both "
                        f"{self._commands[cmd].name!r} and {a.name!r}"
                    )
                self._commands[cmd] = a

    @property
    def channel(self):
        return self._services.channel

    def _svc_for(self, agent: Agent) -> Services:
        return replace(self._services, store=self._services.store.namespaced(agent.name))

    def run_scheduled(self, agent_name: str) -> None:
        agent = self._agents[agent_name]
        try:
            agent.run_scheduled(self._svc_for(agent))
        except Exception as exc:  # noqa: BLE001 - a failing agent must not crash the host
            log.exception("agent %s run_scheduled failed", agent_name)
            try:
                self._services.channel.send(
                    f"⚠️ scheduled {agent_name} failed: {html.escape(str(exc))}")
            except Exception:  # noqa: BLE001 - alerting must never crash the host either
                log.exception("failed to send failure alert for %s", agent_name)

    def _unknown_command_hint(self, cmd: str) -> str:
        available = ", ".join(sorted(self._commands)) or "(none)"
        return (f"Unknown command {cmd}. "
                f"Available commands: {available}. Try /help.")

    def _route(self, msg) -> Agent:
        text = msg.text or ""
        if text.startswith("/"):
            return self._commands[text.split()[0].lower()]  # membership pre-checked
        if getattr(msg, "photo_file_ids", None):
            image_agent = getattr(self._services.config, "image_agent", None)
            if image_agent and image_agent in self._agents:
                return self._agents[image_agent]
        return self._agents[self._default]

    def handle_message(self, update: dict) -> str | None:
        msg = self._services.channel.parse_update(update)
        if msg is None:
            return None
        allowed = getattr(self._services.config, "telegram_chat_id", None)
        if allowed is not None and str(msg.chat_id) != str(allowed):
            log.warning("dropping message from unauthorized chat_id %s", msg.chat_id)
            return None
        text = msg.text or ""
        if text.startswith("/") and text.split()[0].lower() not in self._commands:
            hint = self._unknown_command_hint(text.split()[0])
            self._services.channel.send(hint)
            return hint
        agent = self._route(msg)
        try:
            reply = agent.handle_message(msg, self._svc_for(agent))
        except Exception:  # noqa: BLE001 - isolate agent failures from the host
            log.exception("agent %s handle_message failed", agent.name)
            return None
        if reply:
            self._services.channel.send(reply)
        return reply
