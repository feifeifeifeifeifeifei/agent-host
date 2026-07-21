from agent_host.entrypoints import local_run

class FakeHost:
    def __init__(self): self.ran=[]; self.handled=[]
    def run_scheduled(self, name): self.ran.append(name)
    def handle_message(self, update): self.handled.append(update)

def test_run_subcommand_invokes_run_scheduled():
    host = FakeHost()
    local_run.main(["run", "brief"],
                   build_host=lambda cfg=None: host,
                   load_config=lambda: None)          # no real env needed
    assert host.ran == ["brief"]

def test_serve_once_handles_updates_and_advances_offset():
    host = FakeHost()
    class Ch:
        def get_updates(self, offset):
            return [{"update_id": 7, "message": {"chat": {"id": 1}, "text": "hi"}}]
    next_offset = local_run.serve_once(host, offset=None, channel=Ch())
    assert host.handled and next_offset == 8      # update_id + 1
