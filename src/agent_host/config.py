from typing import Annotated
from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    telegram_chat_id: str
    telegram_webhook_secret: str = ""

    openrouter_api_key: str
    llm_model: str = "deepseek/deepseek-v3.2"
    # NoDecode: stop pydantic-settings from json.loads()-ing the env value at the
    # source level, so our _split_csv before-validator receives the raw CSV string.
    llm_fallback_models: Annotated[list[str], NoDecode] = [
        "qwen/qwen3.6-plus", "google/gemini-2.5-flash"
    ]

    timezone: str = "Asia/Shanghai"
    store_backend: str = "sqlite"          # "sqlite" | "dynamo"
    sqlite_path: str = "agent_host.sqlite"
    dynamo_table: str = "agent_host"

    enabled_agents: Annotated[list[str], NoDecode] = ["brief", "chat"]
    default_agent: str = "chat"
    output_language: str = "zh"

    # --- StockAgent (Phase 01) ---
    finnhub_api_key: str = ""
    stock_max_tickers: int = 50
    stock_mover_threshold_pct: float = 4.0
    stock_max_movers: int = 5
    stock_peer_limit: int = 5
    stock_fetch_workers: int = 8   # max concurrent Yahoo fetches (rate-limit cap)
    stock_schedule_tz: str = "America/Vancouver"   # doc-only; real schedule in EventBridge
    image_agent: str = "stock"                     # which agent consumes photo messages
    vision_model: str = "google/gemini-2.5-flash"   # cheap vision-capable OpenRouter id

    @field_validator("llm_fallback_models", "enabled_agents", mode="before")
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v
