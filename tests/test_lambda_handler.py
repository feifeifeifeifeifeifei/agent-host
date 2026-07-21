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
