from agent_host.config import Config


def test_config_reads_env_and_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "a/x, b/y")  # comma-separated
    cfg = Config()
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "42"
    assert cfg.llm_model == "deepseek/deepseek-v3.2"        # default
    assert cfg.llm_fallback_models == ["a/x", "b/y"]        # split on comma
    assert cfg.enabled_agents == ["brief", "chat"]          # default list
    assert cfg.store_backend == "sqlite"


def test_config_vision_model_and_image_agent_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    cfg = Config()
    assert cfg.vision_model == "google/gemini-2.5-flash"   # default cheap vision id
    assert cfg.image_agent == "stock"                       # default photo consumer


def test_config_vision_model_env_override(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("VISION_MODEL", "vendor/cheap-vision")
    assert Config().vision_model == "vendor/cheap-vision"
