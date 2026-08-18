from dataclasses import dataclass

from agent_host.channels.base import Channel
from agent_host.config import Config
from agent_host.llm import LLMClient
from agent_host.store.base import Store


@dataclass
class Services:
    channel: Channel
    llm: LLMClient
    store: Store
    config: Config
