from agent_host.models import InboundMessage
from agent_host.services import Services


class Agent:
    name: str = "agent"
    schedule: str | None = None       # cron expr if scheduled, else None
    commands: list[str] = []          # slash-commands this agent owns
    intent: str | None = None         # NL description for future LLM routing

    def run_scheduled(self, svc: Services) -> None:
        return None

    def handle_message(self, msg: InboundMessage, svc: Services) -> str | None:
        return None
