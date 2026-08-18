from agent_host.models import ConversationTurn
from agent_host.store.sqlite_store import SqliteStore


def test_memory_roundtrip_and_namespacing(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite"))
    brief = store.namespaced("brief")
    chat = store.namespaced("chat")

    chat.save_memory("42", [ConversationTurn(role="user", content="hi")])
    assert [t.content for t in chat.load_memory("42")] == ["hi"]
    # namespaces are isolated
    assert brief.load_memory("42") == []


def test_prefs_and_seen(tmp_path):
    store = SqliteStore(str(tmp_path / "t.sqlite")).namespaced("brief")
    store.set_prefs("42", {"lang": "zh"})
    assert store.get_prefs("42") == {"lang": "zh"}
    assert store.seen("h1") is False
    store.mark_seen(["h1", "h2"])
    assert store.seen("h1") is True and store.seen("h2") is True
