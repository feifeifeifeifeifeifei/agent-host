from agent_host.host import Host
from agent_host.services import Services
from agent_host.agents.base import Agent
from agent_host.channels.telegram import TelegramChannel

class FakeStore:
    def namespaced(self, agent): return self

def _svc():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    return Services(channel=ch, llm=None, store=FakeStore(), config=None)

def test_routes_command_to_owning_agent():
    class Brief(Agent):
        name = "brief"; commands = ["/brief"]
        def handle_message(self, msg, svc): return "BRIEF"
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"

    svc = _svc()
    host = Host([Brief(), Chat()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "text": "/brief"}})
    assert reply == "BRIEF"
    assert svc.channel.sent[-1]["text"] == "BRIEF"    # host sent the reply

def test_free_text_goes_to_default_agent():
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"
    svc = _svc()
    host = Host([Chat()], svc, default_agent="chat")
    assert host.handle_message({"message": {"chat": {"id": 42}, "text": "hello"}}) == "CHAT"

def test_run_scheduled_dispatches():
    seen = {}
    class Brief(Agent):
        name = "brief"
        def run_scheduled(self, svc): seen["ran"] = True
    host = Host([Brief()], _svc(), default_agent="brief")
    host.run_scheduled("brief")
    assert seen.get("ran") is True

def test_failing_agent_does_not_crash_host():
    class Boom(Agent):
        name = "boom"; commands = ["/boom"]
        def handle_message(self, msg, svc): raise RuntimeError("kaboom")
    host = Host([Boom()], _svc(), default_agent="boom")
    # isolated: returns None instead of propagating
    assert host.handle_message({"message": {"chat": {"id": 42}, "text": "/boom"}}) is None

def test_failing_scheduled_agent_does_not_crash_host():
    class Boom(Agent):
        name = "boom"
        def run_scheduled(self, svc): raise RuntimeError("kaboom")
    host = Host([Boom()], _svc(), default_agent="boom")
    host.run_scheduled("boom")   # does not raise
