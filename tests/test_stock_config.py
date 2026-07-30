# tests/test_stock_config.py
from agent_host.config import Config


def _base_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


def test_stock_config_defaults(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    cfg = Config()
    assert cfg.finnhub_api_key == ""
    assert cfg.stock_max_tickers == 50
    assert cfg.stock_mover_threshold_pct == 4.0
    assert cfg.stock_max_movers == 5
    assert cfg.stock_peer_limit == 5
    assert cfg.stock_schedule_tz == "America/Vancouver"
    assert cfg.image_agent == "stock"
    # vision_model is a Phase 03 addition; it must NOT exist yet.
    assert not hasattr(cfg, "vision_model")


def test_stock_config_env_override(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-secret")
    monkeypatch.setenv("STOCK_MAX_TICKERS", "10")
    monkeypatch.setenv("STOCK_MOVER_THRESHOLD_PCT", "2.5")
    monkeypatch.setenv("IMAGE_AGENT", "stock")
    cfg = Config()
    assert cfg.finnhub_api_key == "fh-secret"
    assert cfg.stock_max_tickers == 10
    assert cfg.stock_mover_threshold_pct == 2.5
    assert cfg.image_agent == "stock"
