import json
from agent_host import registry
from agent_host.config import Config


def _default_load_config() -> Config:
    return Config()


def lambda_handler(event, context=None, build_host=registry.build_host,
                   load_config=_default_load_config):
    cfg = load_config()

    # Scheduled path (EventBridge payload)
    if event.get("mode") == "scheduled":
        host = build_host(cfg)
        host.run_scheduled(event["agent"])
        return {"statusCode": 200, "body": "ok"}

    # HTTP path (Lambda Function URL → Telegram webhook)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if cfg.telegram_webhook_secret:
        got = headers.get("x-telegram-bot-api-secret-token")
        if got != cfg.telegram_webhook_secret:
            return {"statusCode": 403, "body": "forbidden"}

    body = json.loads(event.get("body") or "{}")
    host = build_host(cfg)
    host.handle_message(body)
    return {"statusCode": 200, "body": "ok"}
