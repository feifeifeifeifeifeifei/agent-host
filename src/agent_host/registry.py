from agent_host.config import Config
from agent_host.services import Services
from agent_host.llm import LLMClient
from agent_host.channels.telegram import TelegramChannel
from agent_host.store.base import Store
from agent_host.host import Host


def build_store(config: Config) -> Store:
    if config.store_backend == "sqlite":
        from agent_host.store.sqlite_store import SqliteStore
        return SqliteStore(config.sqlite_path)
    if config.store_backend == "dynamo":
        from agent_host.store.dynamo_store import DynamoStore
        return DynamoStore(config.dynamo_table)
    raise ValueError(f"unknown store_backend: {config.store_backend}")


def build_services(config: Config, dry_run: bool = False) -> Services:
    return Services(
        channel=TelegramChannel(config.telegram_bot_token, config.telegram_chat_id,
                                dry_run=dry_run),
        llm=LLMClient(config.openrouter_api_key, config.llm_model,
                      config.llm_fallback_models),
        store=build_store(config),
        config=config,
    )


def _agent_factories() -> dict:
    # lazy imports so Host tests don't require the concrete agents to exist yet
    from agent_host.agents.brief.agent import BriefAgent
    from agent_host.agents.chat.agent import ChatAgent
    return {"brief": BriefAgent, "chat": ChatAgent}


def build_agents(config: Config) -> list:
    factories = _agent_factories()
    return [factories[name]() for name in config.enabled_agents if name in factories]


def build_host(config: Config, dry_run: bool = False) -> Host:
    agents = build_agents(config)
    if config.default_agent not in {a.name for a in agents}:
        raise ValueError(
            f"default_agent {config.default_agent!r} not in enabled agents "
            f"{[a.name for a in agents]}"
        )
    return Host(agents, build_services(config, dry_run=dry_run),
                default_agent=config.default_agent)
