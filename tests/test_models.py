from agent_host.models import InboundMessage, ConversationTurn, DigestItem


def test_models_construct_with_defaults():
    m = InboundMessage(chat_id="42", text="hi")
    assert m.message_id is None and m.raw == {}
    t = ConversationTurn(role="user", content="hello")
    assert t.role == "user"
    d = DigestItem(source="placeholder", title="T")
    assert d.category == "general" and d.url is None
