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

def test_message_from_unauthorized_chat_id_is_dropped():
    ran = {}
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc):
            ran["called"] = True
            return "CHAT"
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    cfg = type("C", (), {"telegram_chat_id": "42"})()
    svc = Services(channel=ch, llm=None, store=FakeStore(), config=cfg)
    host = Host([Chat()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 999}, "text": "hello"}})
    assert reply is None
    assert "called" not in ran            # agent never ran
    assert ch.sent == []                  # nothing was sent

def test_message_from_authorized_chat_id_is_still_handled():
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    cfg = type("C", (), {"telegram_chat_id": "42"})()
    svc = Services(channel=ch, llm=None, store=FakeStore(), config=cfg)
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"
    host = Host([Chat()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "text": "hello"}})
    assert reply == "CHAT"
    assert ch.sent[-1]["text"] == "CHAT"

import pytest


def test_unknown_command_returns_and_sends_hint():
    class Chat(Agent):
        name = "chat"; commands = ["/help"]
        def handle_message(self, msg, svc): return "CHAT"
    svc = _svc()
    host = Host([Chat()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "text": "/nope arg"}})
    assert reply is not None
    assert "Unknown command" in reply and "/nope" in reply and "/help" in reply
    assert svc.channel.sent[-1]["text"] == reply          # hint was actually sent


def test_photo_message_routes_to_image_agent():
    captured = {}
    class Stock(Agent):
        name = "stock"; commands = ["/confirm"]
        def handle_message(self, msg, svc):
            captured["photos"] = msg.photo_file_ids
            return "PHOTO OK"
    class Chat(Agent):
        name = "chat"
        def handle_message(self, msg, svc): return "CHAT"
    ch = TelegramChannel(token="t", chat_id="42", dry_run=True)
    cfg = type("C", (), {"telegram_chat_id": "42", "image_agent": "stock"})()
    svc = Services(channel=ch, llm=None, store=FakeStore(), config=cfg)
    host = Host([Chat(), Stock()], svc, default_agent="chat")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "photo": [
        {"file_id": "small", "file_size": 100},
        {"file_id": "big", "file_size": 900},
    ]}})
    assert reply == "PHOTO OK"
    assert captured["photos"] == ["big"]                  # routed with the file_id


def test_mixed_case_command_routes_to_owning_agent_not_unknown_hint():
    class Stock(Agent):
        name = "stock"; commands = ["/tickers"]
        def handle_message(self, msg, svc): return "TICKERS OK"
    svc = _svc()
    host = Host([Stock()], svc, default_agent="stock")
    reply = host.handle_message({"message": {"chat": {"id": 42}, "text": "/TICKERS"}})
    assert reply == "TICKERS OK"
    assert "Unknown command" not in (svc.channel.sent[-1]["text"] if svc.channel.sent else "")


def test_duplicate_command_across_agents_raises():
    class A(Agent):
        name = "a"; commands = ["/dup"]
    class B(Agent):
        name = "b"; commands = ["/dup"]
    with pytest.raises(ValueError, match="/dup"):
        Host([A(), B()], _svc(), default_agent="a")
