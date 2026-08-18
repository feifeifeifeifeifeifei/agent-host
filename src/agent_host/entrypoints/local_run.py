import sys

from agent_host import registry
from agent_host.config import Config


def serve_once(host, offset, channel):
    updates = channel.get_updates(offset)
    new_offset = offset
    for u in updates:
        host.handle_message(u)
        new_offset = u["update_id"] + 1
    return new_offset


def main(argv=None, build_host=registry.build_host, load_config=Config):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: local_run (run <agent> | serve)")
        return
    cmd = argv[0]
    cfg = load_config()
    host = build_host(cfg)
    if cmd == "run":
        host.run_scheduled(argv[1])
    elif cmd == "serve":
        # host._services is internal; expose the channel via a small accessor
        channel = host.channel
        offset = None
        print("serving (long-poll). Ctrl-C to stop.")
        while True:
            offset = serve_once(host, offset, channel)
    else:
        print(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
