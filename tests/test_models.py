from agent_host.models import InboundMessage, ConversationTurn, DigestItem


def test_models_construct_with_defaults():
    m = InboundMessage(chat_id="42", text="hi")
    assert m.message_id is None and m.raw == {}
    t = ConversationTurn(role="user", content="hello")
    assert t.role == "user"
    d = DigestItem(source="placeholder", title="T")
    assert d.category == "general" and d.url is None


def test_inbound_message_photo_file_ids_default_and_populated():
    m = InboundMessage(chat_id="42", text="hi")
    assert m.photo_file_ids == []          # additive default keeps old callers green
    m2 = InboundMessage(chat_id="42", text="", photo_file_ids=["big-file-id"])
    assert m2.photo_file_ids == ["big-file-id"]
