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
        self._commands = {cmd: a for a in agents for cmd in a.commands}

    @property
    def channel(self):
        return self._services.channel

    def _svc_for(self, agent: Agent) -> Services:
        return replace(self._services, store=self._services.store.namespaced(agent.name))

    def run_scheduled(self, agent_name: str) -> None:
        agent = self._agents[agent_name]
        try:
            agent.run_scheduled(self._svc_for(agent))
        except Exception:  # noqa: BLE001 - a failing agent must not crash the host
            log.exception("agent %s run_scheduled failed", agent_name)

    def _route(self, text: str) -> Agent:
        if text.startswith("/"):
            cmd = text.split()[0]
            if cmd in self._commands:
                return self._commands[cmd]
        return self._agents[self._default]

    def handle_message(self, update: dict) -> str | None:
        msg = self._services.channel.parse_update(update)
        if msg is None:
            return None
        agent = self._route(msg.text)
        try:
            reply = agent.handle_message(msg, self._svc_for(agent))
        except Exception:  # noqa: BLE001 - isolate agent failures from the host
            log.exception("agent %s handle_message failed", agent.name)
            return None
        if reply:
            self._services.channel.send(reply)
        return reply
