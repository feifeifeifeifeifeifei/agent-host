from agent_host.agents.brief.sources.placeholder import PlaceholderSource
from agent_host.models import DigestItem


def test_placeholder_returns_digest_items():
    items = PlaceholderSource().fetch()
    assert len(items) >= 1
    assert all(isinstance(i, DigestItem) for i in items)
    assert all(i.source == "placeholder" for i in items)
