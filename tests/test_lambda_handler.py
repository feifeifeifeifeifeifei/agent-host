import json

from agent_host.entrypoints import lambda_handler as lh


class FakeHost:
    def __init__(self): self.ran=[]; self.handled=[]
    def run_scheduled(self, name): self.ran.append(name)
    def handle_message(self, body): self.handled.append(body)

class FakeCfg:
    telegram_webhook_secret = "s3cr3t"
    store_backend = "dynamo"

def _handler_with(host):
    return lambda event, context=None: lh.lambda_handler(
        event, context, build_host=lambda cfg: host, load_config=lambda: FakeCfg())

def test_scheduled_event_runs_agent():
    host = FakeHost()
    resp = _handler_with(host)({"mode": "scheduled", "agent": "brief"})
    assert resp["statusCode"] == 200 and host.ran == ["brief"]

def test_http_event_with_good_secret_handles_message():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"},
             "body": json.dumps({"message": {"chat": {"id": 1}, "text": "hi"}})}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 200 and host.handled

def test_http_event_with_bad_secret_is_rejected():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "wrong"},
             "body": "{}"}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 403 and not host.handled


class FakeCfgNoSecret:
    telegram_webhook_secret = ""
    store_backend = "dynamo"


def test_http_event_with_unconfigured_secret_fails_closed():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "anything"},
             "body": json.dumps({"message": {"chat": {"id": 1}, "text": "hi"}})}
    resp = lh.lambda_handler(event, None, build_host=lambda cfg: host,
                              load_config=lambda: FakeCfgNoSecret())
    assert resp["statusCode"] == 403 and not host.handled


def test_http_event_with_malformed_json_returns_400():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"},
             "body": "{not valid json"}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 400 and not host.handled


def test_http_event_with_base64_encoded_body_is_decoded():
    import base64
    host = FakeHost()
    payload = json.dumps({"message": {"chat": {"id": 1}, "text": "hi"}})
    event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"},
             "isBase64Encoded": True,
             "body": base64.b64encode(payload.encode()).decode()}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 200 and host.handled


def test_http_event_with_invalid_base64_returns_400():
    host = FakeHost()
    event = {"headers": {"x-telegram-bot-api-secret-token": "s3cr3t"},
             "isBase64Encoded": True,
             "body": "not-valid-base64!!!"}
    resp = _handler_with(host)(event)
    assert resp["statusCode"] == 400 and not host.handled


def test_default_build_host_is_cached_across_warm_invocations(monkeypatch):
    lh._HOST = None                      # 重置 module 缓存
    calls = []
    host = FakeHost()

    def counting_build_host(cfg):
        calls.append(1)
        return host

    monkeypatch.setattr(lh.registry, "build_host", counting_build_host)
    monkeypatch.setattr(lh, "_default_load_config", lambda: FakeCfg())

    ev = {"mode": "scheduled", "agent": "brief"}
    lh.lambda_handler(ev)                # 默认路径:构造一次并缓存
    lh.lambda_handler(ev)                # warm:复用缓存
    assert len(calls) == 1
    assert host.ran == ["brief", "brief"]


def test_injected_build_host_is_never_cached():
    lh._HOST = None
    calls = []

    def bh(cfg):
        calls.append(1)
        return FakeHost()

    run = lambda ev: lh.lambda_handler(ev, build_host=bh, load_config=lambda: FakeCfg())
    run({"mode": "scheduled", "agent": "brief"})
    run({"mode": "scheduled", "agent": "brief"})
    assert len(calls) == 2               # 注入路径每次都构造
    assert lh._HOST is None              # module 缓存未被污染
