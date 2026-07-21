# tests/test_brief_agent.py
from agent_host.agents.brief.agent import BriefAgent
from agent_host.agents.brief.sources.base import Source
from agent_host.services import Services
from agent_host.channels.telegram import TelegramChannel
from agent_host.models import DigestItem, InboundMessage

class OneItem(Source):
    name = "one"
    def fetch(self): return [DigestItem(source="one", title="T1", url="u1")]

class Boom(Source):
    name = "boom"
    def fetch(self): raise RuntimeError("feed down")

class StubLLM:
    def complete(self, messages): return "<b>BRIEF</b>"

class MemStore:
    def __init__(self): self._seen=set(); self.runs=[]
    def namespaced(self, a): return self
    def seen(self, k): return k in self._seen
    def mark_seen(self, ks): self._seen.update(ks)
    def get_prefs(self, c): return {}
    def record_run(self, meta): self.runs.append(meta)

def _svc(store):
    return Services(channel=TelegramChannel("t","42",dry_run=True),
                    llm=StubLLM(), store=store, config=type("C",(),{"output_language":"zh"}))

def test_run_scheduled_sends_and_survives_bad_source():
    store = MemStore()
    svc = _svc(store)
    BriefAgent(sources=[OneItem(), Boom()]).run_scheduled(svc)
    assert svc.channel.sent[-1]["text"] == "<b>BRIEF</b>"   # bad source didn't crash
    assert len(store.runs) == 1

def test_run_scheduled_dedups_second_run():
    store = MemStore()
    agent = BriefAgent(sources=[OneItem()])
    agent.run_scheduled(_svc(store))          # first run: item is fresh
    svc2 = _svc(store)                          # same store (seen persists)
    svc2.store = store
    agent.run_scheduled(svc2)
    # second run: only item already seen -> composer got empty list -> "no news"
    assert "没有新的要闻" in svc2.channel.sent[-1]["text"]

def test_handle_message_returns_brief_without_dedup():
    store = MemStore()
    reply = BriefAgent(sources=[OneItem()]).handle_message(
        InboundMessage(chat_id="42", text="/brief"), _svc(store))
    assert reply == "<b>BRIEF</b>"
