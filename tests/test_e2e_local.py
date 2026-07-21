import os
import pytest
from agent_host.config import Config
from agent_host.registry import build_host

@pytest.mark.skipif(
    os.getenv("RUN_E2E") != "1",
    reason="set RUN_E2E=1 with a real .env to perform a live Telegram send",
)
def test_real_local_brief_send():
    host = build_host(Config())
    host.run_scheduled("brief")          # sends a real message to your Telegram
