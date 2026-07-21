from abc import ABC, abstractmethod
from agent_host.models import DigestItem


class Source(ABC):
    name: str = "source"

    @abstractmethod
    def fetch(self) -> list[DigestItem]: ...
