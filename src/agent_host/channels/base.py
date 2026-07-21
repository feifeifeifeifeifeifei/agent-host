from abc import ABC, abstractmethod
from agent_host.models import InboundMessage


class Channel(ABC):
    @abstractmethod
    def send(self, text: str) -> None: ...
    @abstractmethod
    def parse_update(self, raw: dict) -> InboundMessage | None: ...
