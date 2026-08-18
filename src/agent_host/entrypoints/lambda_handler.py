import base64
import hmac
import json

from agent_host import registry
from agent_host.config import Config


def _default_load_config() -> Config:
    return Config()


_HOST = None   # module-scope cache: reused across warm invocations (default build_host path only)


def _get_host(cfg, build_host):
    """Default path caches and reuses; injected build_host (tests) is never cached."""
    global _HOST
    if build_host is not None:
        return build_host(cfg)
    if _HOST is None:
        _HOST = registry.build_host(cfg)
    return _HOST


def lambda_handler(event, context=None, build_host=None, load_config=None):
    cfg = (load_config or _default_load_config)()

    # Scheduled path (EventBridge payload)
    if event.get("mode") == "scheduled":
        host = _get_host(cfg, build_host)
        host.run_scheduled(event["agent"])
        return {"statusCode": 200, "body": "ok"}

    # HTTP path (Lambda Function URL → Telegram webhook)
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    secret = cfg.telegram_webhook_secret
    got = headers.get("x-telegram-bot-api-secret-token") or ""
    if not secret or not hmac.compare_digest(got, secret):
        # fail closed: an unconfigured secret leaves a public (auth=NONE) URL open
        return {"statusCode": 403, "body": "forbidden"}

    raw = event.get("body") or ""
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        body = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {"statusCode": 400, "body": "bad request"}
    host = _get_host(cfg, build_host)
    host.handle_message(body)
    return {"statusCode": 200, "body": "ok"}
