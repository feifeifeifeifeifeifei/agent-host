# tests/test_chat_agent.py
from agent_host.agents.chat.agent import ChatAgent
from agent_host.services import Services
from agent_host.models import InboundMessage, ConversationTurn

class StubLLM:
    def __init__(self): self.messages=None
    def complete(self, messages):
        self.messages = messages
        return "你好,我在。"

class MemStore:
    def __init__(self): self._m={}
    def namespaced(self, a): return self
    def load_memory(self, c): return self._m.get(c, [])
    def save_memory(self, c, turns): self._m[c] = turns

def test_reply_and_memory_persist():
    llm = StubLLM(); store = MemStore()
    svc = Services(channel=None, llm=llm, store=store,
                   config=type("C", (), {"output_language": "zh"}))
    agent = ChatAgent()
    reply = agent.handle_message(InboundMessage(chat_id="42", text="在吗"), svc)
    assert reply == "你好,我在。"
    # system prompt present + user message forwarded
    assert llm.messages[0]["role"] == "system"
    assert llm.messages[-1] == {"role": "user", "content": "在吗"}
    # both turns saved
    saved = store.load_memory("42")
    assert [t.role for t in saved] == ["user", "assistant"]
    assert saved[1].content == "你好,我在。"

def test_history_is_replayed_and_trimmed():
    llm = StubLLM(); store = MemStore()
    store.save_memory("42", [ConversationTurn(role="user", content="prior")])
    svc = Services(channel=None, llm=llm, store=store,
                   config=type("C", (), {"output_language": "zh"}))
    ChatAgent(max_turns=2).handle_message(InboundMessage(chat_id="42", text="new"), svc)
    # prior history replayed between system and the new user message
    assert {"role": "user", "content": "prior"} in llm.messages
    # trimmed to last 2 turns
    assert len(store.load_memory("42")) == 2
