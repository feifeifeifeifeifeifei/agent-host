# agent-host

`agent-host` is a small, pluggable **host** for running Telegram-connected AI agents. The host
owns the shared plumbing — a Telegram channel, an OpenRouter-backed LLM client, a storage backend
(SQLite locally, DynamoDB in the cloud), and message routing — while individual **agents** plug
into it to do actual work. Two agents ship out of the box: `BriefAgent`, which composes and sends
a daily news brief on a schedule, and `ChatAgent`, which holds free-form conversations with
per-chat memory. The same code runs two ways: as a local long-polling process for development, or
as an AWS Lambda function (Telegram webhook + EventBridge daily schedule) in production — see
[`infra/aws-runbook.md`](infra/aws-runbook.md) for the click-by-click deploy guide.

## Local quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env
# then edit .env and fill in real values (see below)
```

You need, at minimum:

- **`TELEGRAM_BOT_TOKEN`** — message [@BotFather](https://t.me/BotFather) on Telegram, run
  `/newbot`, and it will hand you a token.
- **`TELEGRAM_CHAT_ID`** — send your new bot any message first (bots can't message you until you
  message them), then visit
  `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read `message.chat.id` from
  the JSON response. (Or just message [@userinfobot](https://t.me/userinfobot) to get your own
  chat ID directly.)
- **`OPENROUTER_API_KEY`** — from https://openrouter.ai/keys. The default model is
  `deepseek/deepseek-v3.2` (see `LLM_MODEL` in `.env.example`), with `qwen/qwen3.6-plus` and
  `google/gemini-2.5-flash` configured as automatic fallback models if the primary is unavailable.

Everything else in `.env.example` has a sensible local default (`STORE_BACKEND=sqlite`,
`TIMEZONE=Asia/Shanghai`, `ENABLED_AGENTS=brief, chat`, `DEFAULT_AGENT=chat`, ...) — leave those
as-is to start.

Run the tests:

```bash
pytest
```

One test (`tests/test_e2e_local.py`) is skipped by default because it sends a **real** message to
your real Telegram chat — it only runs if you export `RUN_E2E=1` with a fully-populated `.env`.

Try the agents locally:

```bash
# Run the brief agent once, right now, from the command line:
python -m agent_host.entrypoints.local_run run brief

# Or run a long-poll loop that answers real Telegram messages (Ctrl-C to stop):
python -m agent_host.entrypoints.local_run serve
```

For `serve` to receive anything, open Telegram and send your bot a `/start` (or any message) —
Telegram only delivers messages to a bot after a user has messaged it at least once. Send it
`/brief` to get the news brief on demand, or just chat with it normally (routed to `ChatAgent` by
default).

## How to add a new agent

An agent is any class implementing the `Agent` interface (`src/agent_host/agents/base.py`):

```python
class Agent:
    name: str = "agent"
    schedule: str | None = None       # cron expr, informational — actual scheduling is external (EventBridge)
    commands: list[str] = []          # slash-commands this agent owns, e.g. ["/brief"]
    intent: str | None = None         # NL description, for future LLM-based routing

    def run_scheduled(self, svc: Services) -> None: ...
    def handle_message(self, msg: InboundMessage, svc: Services) -> str | None: ...
```

To add your own:

1. **Subclass `Agent`** somewhere under `src/agent_host/agents/` (follow the existing
   `agents/brief/` or `agents/chat/` package layout as a template). Set `name` to a short unique
   string — this is the key used everywhere the agent is looked up.
2. **Implement whichever entry points apply:**
   - `run_scheduled(self, svc)` — called for scheduled/cron-triggered work (e.g. what
     `BriefAgent` does once a day). `svc` is a `Services` bundle giving you `svc.channel` (send
     messages), `svc.llm` (call the configured LLM), `svc.store` (a `Store`, already namespaced to
     your agent's `name` so your keys can't collide with another agent's), and `svc.config`.
   - `handle_message(self, msg, svc)` — called when this agent is routed an inbound Telegram
     message (`msg: InboundMessage` has `.chat_id`, `.text`, `.message_id`, `.raw`). Return a
     `str` to have the `Host` send it back as the reply, or `None` to send nothing.
   - Set `commands = ["/yourcommand"]` if you want the `Host` to route messages starting with that
     slash-command straight to your agent regardless of the default agent (see `Host._route` in
     `src/agent_host/host.py`); otherwise your agent only receives messages when it's the
     configured `DEFAULT_AGENT`.
3. **Register it** in `src/agent_host/registry.py`. There is **no** module-level
   `AGENT_FACTORIES` constant — the registry point is the `_agent_factories()` function:
   ```python
   def _agent_factories() -> dict:
       from agent_host.agents.brief.agent import BriefAgent
       from agent_host.agents.chat.agent import ChatAgent
       from agent_host.agents.yours.agent import YourAgent   # add your import
       return {"brief": BriefAgent, "chat": ChatAgent, "yours": YourAgent}  # add your entry
   ```
   `build_agents()` only instantiates agents whose name appears in this dict **and** in
   `config.enabled_agents`, so both steps below are required.
4. **Enable it** by adding its name to `ENABLED_AGENTS` in `.env` (comma-separated, e.g.
   `ENABLED_AGENTS=brief, chat, yours`) — this is read by `Config.enabled_agents` and is what
   `build_agents()` filters against.
5. If it should run on a schedule in production, add a matching EventBridge schedule targeting the
   Lambda with input `{"mode": "scheduled", "agent": "yours"}` — see
   [`infra/aws-runbook.md`, Section 8](infra/aws-runbook.md#8-create-the-daily-schedule-eventbridge).

Write a test under `tests/` alongside the existing `test_brief_agent.py` / `test_chat_agent.py` as
a model — the host and services are simple enough to construct directly with fakes/dry-run mode,
no AWS or network access required.

## Deploying to AWS

Local `serve` (long-polling) is fine for development but isn't meant to run unattended forever.
For a real always-on deployment — Telegram webhook via a Lambda Function URL, plus a daily
EventBridge schedule for the brief, backed by DynamoDB instead of the local SQLite file — follow
the full click-by-click guide in **[`infra/aws-runbook.md`](infra/aws-runbook.md)**. It covers
prerequisites, the DynamoDB table, the IAM role, packaging and uploading the Lambda zip, wiring up
the webhook, the EventBridge cron schedule, end-to-end verification, and cost/teardown.
