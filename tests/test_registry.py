import pytest
from agent_host import registry


class StubConfig:
    telegram_bot_token = "t"
    telegram_chat_id = "42"
    telegram_webhook_secret = ""
    openrouter_api_key = "k"
    llm_model = "deepseek/deepseek-v3.2"
    llm_fallback_models = []
    store_backend = "sqlite"
    enabled_agents = ["brief"]
    default_agent = "chat"          # not in enabled_agents -> should raise

    def __init__(self, sqlite_path):
        self.sqlite_path = sqlite_path


def test_build_host_raises_on_misconfigured_default_agent(tmp_path):
    cfg = StubConfig(sqlite_path=str(tmp_path / "agent_host.sqlite"))
    with pytest.raises(ValueError, match="default_agent"):
        registry.build_host(cfg)


def test_stock_factory_is_registered():
    factories = registry._agent_factories()
    assert "stock" in factories
    # no-arg constructable, like every other factory entry
    agent = factories["stock"]()
    assert agent.name == "stock"


class StockEnabledConfig:
    telegram_bot_token = "t"
    telegram_chat_id = "42"
    telegram_webhook_secret = ""
    openrouter_api_key = "k"
    llm_model = "deepseek/deepseek-v3.2"
    llm_fallback_models = []
    vision_model = "google/gemini-2.5-flash"
    store_backend = "sqlite"
    enabled_agents = ["stock", "chat"]
    default_agent = "chat"

    def __init__(self, sqlite_path):
        self.sqlite_path = sqlite_path


def test_build_host_includes_stock_when_enabled(tmp_path):
    cfg = StockEnabledConfig(sqlite_path=str(tmp_path / "agent_host.sqlite"))
    host = registry.build_host(cfg)
    names = {a.name for a in host._agents.values()}
    assert "stock" in names
    # command routing wired: /tickers resolves to the stock agent
    assert host._commands["/tickers"].name == "stock"
