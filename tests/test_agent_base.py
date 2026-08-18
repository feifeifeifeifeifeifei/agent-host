from agent_host.agents.base import Agent
from agent_host.services import Services


def test_agent_defaults_and_override():
    class Echo(Agent):
        name = "echo"
        commands = ["/echo"]
        def handle_message(self, msg, svc):
            return f"echo: {msg.text}"

    a = Echo()
    assert a.name == "echo" and a.schedule is None and a.commands == ["/echo"]
    # base defaults are safe no-ops
    assert Agent.run_scheduled(a, None) is None

def test_services_is_a_container():
    svc = Services(channel="c", llm="l", store="s", config="cfg")
    assert svc.channel == "c" and svc.store == "s"
