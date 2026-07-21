from agent_host.agents.brief.composer import Composer
from agent_host.models import DigestItem


class StubLLM:
    def __init__(self, out="<b>今日要闻</b>"):
        self.out = out
        self.messages = None

    def complete(self, messages):
        self.messages = messages
        return self.out


def test_compose_returns_llm_html_for_items():
    llm = StubLLM()
    html = Composer(llm).compose([DigestItem(source="s", title="T", summary="S")])
    assert "今日要闻" in html


def test_compose_escapes_item_text_in_prompt():
    llm = StubLLM()
    Composer(llm).compose([DigestItem(source="s", title="A & B <c>")])
    prompt_text = " ".join(m["content"] for m in llm.messages)
    assert "A &amp; B &lt;c&gt;" in prompt_text
    assert "A & B <c>" not in prompt_text


def test_compose_empty_items_skips_llm():
    llm = StubLLM()
    html = Composer(llm).compose([])
    assert llm.messages is None            # LLM not called
    assert html                            # returns a non-empty "no news" message
