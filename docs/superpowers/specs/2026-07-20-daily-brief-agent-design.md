# Personal Agent Host — Design

**Date:** 2026-07-20
**Status:** Approved design (pre-implementation)
**Author:** feiren + Claude

A personal **agent host**: a small runtime that hosts pluggable **agents** and gives them
shared infrastructure (a Telegram channel, an LLM client, storage, config, scheduling).
The first agent is a **Daily Brief** agent that pushes a scheduled brief and can be chatted
with. The whole point of the architecture is that **I can add new agents later without
knowing today what they will be** — a new agent is one new class plus one line in a
registry, and it inherits all the shared plumbing (delivery, LLM, memory, scheduling)
for free. No core rewrite, ever.

A secondary, explicit goal: **learn AWS** (Lambda / EventBridge / DynamoDB / IAM) by
deploying this, because it matters for my resume. Any deployment step I must perform
myself is written click-by-click (see `infra/aws-runbook.md`, produced in the plan).

---

## 1. Goals & Non-Goals

### Goals
- **Host multiple agents** behind one runtime, each an isolated, pluggable unit.
- **Add a new agent seamlessly** = implement one interface + register it + toggle in config.
  New agents automatically get delivery, LLM access, memory, and scheduling.
- First agent — **Daily Brief**: one automatic brief per day on Telegram, at a configured
  time/timezone, that I can also chat with.
- **Pluggable delivery channels** (Telegram now; WeChat/WhatsApp later = one file each).
- **Runtime-agnostic core**: identical code runs locally (dev) and on AWS Lambda (prod);
  only config + the entry shell differ.
- Cost target: **≈ free** (LLM a few cents/month; AWS free tier; data on free tiers).

### Non-Goals (out of scope now)
- Real curated news sourcing and personalized filtering — **Phase 2**; placeholder for MVP.
- LinkedIn / job listings — **not in MVP**. Future direction: a future agent watches my
  email inbox for LinkedIn *job-alert emails* and parses those (legal, uses mail I already
  receive) — NOT the LinkedIn API (none exists for individuals) and NOT scraping.
- WeChat / WhatsApp delivery — designed-for, not built. Personal WeChat can't do two-way
  conversation for an individual dev, so Telegram is the interactive channel.
- Multi-user / productization. Single-user tool for me.
- No coupling to any of my other projects. This repo is fully standalone.

---

## 2. Key Decisions & Rationale

| Decision | Choice | Why |
|---|---|---|
| **Shape** | **Agent host + pluggable agents** | I don't yet know what agents I'll want. A thin host owning shared services + an `Agent` interface means future capabilities drop in without touching the core. |
| **Channel** | **Telegram** (push + two-way) | Only trivially two-way channel (native webhook), free, no verification. Personal WeChat can't do two-way for an individual; 公众号 blocked since 2025-07; 企业微信 replies live in the WeCom app, not personal 微信. Trade-off accepted: build the habit of checking Telegram. |
| **LLM gateway** | **OpenRouter** (OpenAI-compatible) | One key/base_url → 300+ models; model swap = one string. One-time $10 credit unlocks 1,000 req/day free tier as a backstop. |
| **Default model** | **`deepseek/deepseek-v3.2`** ($0.21/$0.32 per 1M) | Cheapest capable option, strong at Chinese finance/geopolitics phrasing, very low output price. Fallback chain: `qwen3.x-plus` → `google/gemini-2.5-flash` (1M context for future full-text dumps). Reversible: config-driven. |
| **Hosting** | **AWS serverless** | Reliable cloud cron independent of my laptop; and a deliberate AWS learning exercise. Lambda (compute) + EventBridge Scheduler (triggers) + Lambda Function URL (webhook) + DynamoDB (state). |
| **MVP content** | **`PlaceholderSource`** + free RSS later | Get the whole pipeline working day one; swap in real sources without touching the core. |
| **Language/stack** | **Python 3.12** | `openai` SDK, Pydantic, pytest. Light deps (`httpx`, `openai`, `feedparser`, `pydantic`) so serverless packaging stays simple. |

---

## 3. Architecture

A thin **Host** owns shared **Services** and a **registry of Agents**. Two runtime paths —
scheduled and conversational — both dispatch into agents. The core imports nothing
AWS- or Telegram-server-specific; deployment code is a thin shell.

```
SHARED SERVICES (built once from config)
  channel (Telegram)   llm (OpenRouter)   store (namespaced per agent)   config

SCHEDULED PATH                              CONVERSATIONAL PATH
──────────────                              ───────────────────
EventBridge rule per scheduled agent        I message the bot
  payload {"mode":"scheduled",                      │
           "agent":"brief"}                          ▼
        │                               Telegram → Function URL (webhook)
        ▼                                            │
 host.run_scheduled("brief")                         ▼
        │                                  host.handle_message(update)
        ▼                                            │
 registry["brief"].run_scheduled(svc)                ├─ command? (/brief) → that agent
        │                                            ├─ else → default ChatAgent
        └─ agent does its work,                      ▼
           delivers via svc.channel        registry[agent].handle_message(msg, svc)
                                                     │
                                                     └─ reply via svc.channel; persist memory
```

### Why these boundaries
Each unit has one purpose, a small interface, and is testable alone:
- Add an agent → add one class in `agents/` + register. Nothing else changes.
- Swap the channel → touch only `channels/`.
- Change model → change config.
- Move local↔cloud → change only the `Store` backend + entry shell.

---

## 4. Components & Interfaces

### 4.1 `Agent` (the pluggable unit)
```python
class Agent(Protocol):
    name: str                      # unique id, e.g. "brief", "chat"
    schedule: str | None           # cron expr if it runs on a timer, else None
    commands: list[str]            # slash-commands it owns, e.g. ["/brief"]; may be []
    intent: str | None             # NL description for future LLM-based routing (optional)

    def run_scheduled(self, svc: "Services") -> None: ...
        # called on the agent's cron; no-op if schedule is None

    def handle_message(self, msg: "InboundMessage", svc: "Services") -> str | None: ...
        # return reply text if this agent handled the message, else None
```
An agent may be scheduled-only, conversational-only, or both. It receives everything it
needs through `Services` (dependency injection) — it never constructs channels/clients
itself, which keeps it isolated and unit-testable.

### 4.2 `Host` + registry + routing
- **Registry:** a single place listing agent instances; config enables/disables by name.
  Adding an agent = write the class + add it here (or auto-discover the `agents/` package).
- **`host.run_scheduled(agent_name)`:** look up the agent, call `run_scheduled(svc)`.
- **`host.handle_message(update)`:** parse via the channel; route —
  (1) if text starts with a slash-command an agent owns → that agent;
  (2) else → the default `ChatAgent`;
  (3) *future:* an LLM router picks by `intent`.

### 4.3 `Services` (shared, injected into agents)
```python
@dataclass
class Services:
    channel: Channel
    llm: LLMClient
    store: Store          # use store.namespaced(agent.name) so agents don't collide
    config: Config
```

### 4.4 `Channel` (delivery)
```python
class Channel(Protocol):
    def send(self, text: str) -> None: ...
    def parse_update(self, raw: dict) -> InboundMessage | None: ...
```
- `TelegramChannel`: `send` = one HTTPS POST to `sendMessage` with `parse_mode=HTML`
  (HTML over MarkdownV2 to avoid escaping 18 special chars). `parse_update` reads the
  webhook JSON into `InboundMessage {chat_id, text, ...}`. Webhook verified via Telegram's
  `secret_token` header.
- Later (one file + config toggle): `ServerChanChannel` (WeChat push mirror), `WhatsAppChannel`.

### 4.5 `LLMClient`
- Thin wrapper over the OpenAI SDK: `base_url=https://openrouter.ai/api/v1`, one key,
  `model` from config, OpenRouter `models` array for automatic fallback. Retry + backoff.
- Shared by every agent.

### 4.6 `Store` (state)
```python
class Store(Protocol):
    def namespaced(self, agent: str) -> "Store": ...   # per-agent key prefix
    def load_memory(self, chat_id: str) -> list[ConversationTurn]: ...
    def save_memory(self, chat_id: str, turns: list[ConversationTurn]) -> None: ...
    def get_prefs(self, chat_id: str) -> dict: ...
    def set_prefs(self, chat_id: str, prefs: dict) -> None: ...
    def seen(self, key: str) -> bool: ...              # generic dedup
    def mark_seen(self, keys: list[str]) -> None: ...
    def record_run(self, meta: dict) -> None: ...
```
- Local/dev: `SqliteStore` (single file, easy to inspect).
- Cloud: `DynamoStore` (serverless-native, free tier ample). Same interface → config switch.

### 4.7 `Config`
- Env-based (`.env` locally; Lambda env vars + secrets in prod), typed via Pydantic.
- Global keys: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`,
  `OPENROUTER_API_KEY`, `LLM_MODEL`, `LLM_FALLBACK_MODELS`, `TIMEZONE`, `STORE_BACKEND`,
  `ENABLED_AGENTS`. Per-agent config lives in its own section. Secrets never committed.

### 4.8 Entrypoints (thin deployment shells)
- `entrypoints/local_run.py`: `run <agent>` (trigger one agent's scheduled run now) and
  `serve` (long-poll `getUpdates` to test conversation locally — **no public webhook for dev**).
- `entrypoints/lambda_handler.py`: routes AWS events — scheduled event → `host.run_scheduled(agent)`;
  HTTP (Function URL) event → `host.handle_message(update)`.

---

## 5. The MVP agents

### `BriefAgent` (scheduled + conversational)
- `schedule`: daily cron. `commands`: `["/brief"]`.
- `run_scheduled`: for each enabled `Source` → `fetch()` (try/except) → dedup → `Composer`
  (LLM) formats an HTML brief → `svc.channel.send()` → `store.record_run()`.
- `handle_message` (for `/brief`): build and send a brief on demand.
- Owns its own building blocks (kept inside the agent, since they're brief-specific):
  - `Source` interface `fetch() -> list[DigestItem]`; MVP ships `PlaceholderSource`
    returning canned items. `DigestItem = {source, title, url, published_at, summary,
    category, raw}`. Phase 2 adapters: `RssSource` (Fed press/FOMC, arXiv cs.AI,
    OpenAI/Google/HF blogs), `FinnhubSource`, `FredSource`, `NewsDataSource`.
  - `Composer`: items + prefs → LLM → HTML brief. Testable with a stubbed LLM.

### `ChatAgent` (conversational, default handler)
- `schedule`: None. `commands`: [] (it's the fallback for free text).
- `handle_message`: load last-N-turn memory + prefs, call LLM with a system prompt
  establishing it as *my* assistant, reply, persist turns.
- Phase 3: tools via function-calling (`fetch_latest_brief`, `update_preferences`, ...).

---

## 6. MVP Scope (Phase 1) — the "placeholder" deliverable

Definition of done:
1. Telegram bot exists; one-time `/start` done.
2. Host runs with two registered agents: `BriefAgent` (placeholder content) + `ChatAgent`.
3. `local_run.py run brief` sends a nicely formatted **placeholder** brief via DeepSeek.
4. `local_run.py serve` lets me chat locally; `/brief` triggers a brief; free text → ChatAgent.
5. `SqliteStore` persists memory + run records (namespaced per agent).
6. Tests: source fixture → composer (stubbed LLM) → channel dry-run; host routing
   (command vs default); one real end-to-end local send.
7. **Then** deploy to AWS: Lambda + EventBridge daily trigger + Function URL webhook +
   DynamoDB, following click-by-click `infra/aws-runbook.md`. Verify a real scheduled push
   and a real webhook conversation in the cloud.

Everything else (real sources, filtering, preferences, tools, more agents, WeChat mirror,
email-based LinkedIn agent) is deliberately deferred — and by design, each is an additive
change, not a rewrite.

---

## 7. Phasing
- **Phase 1 (MVP):** host + `BriefAgent` (placeholder) + `ChatAgent`, local then AWS. ← build first.
- **Phase 2:** real `Source` adapters for `BriefAgent`; persisted preferences; chat can
  read/adjust preferences.
- **Phase 3:** conversation tools; LLM-based message routing across agents; optional
  `ServerChanChannel` WeChat push mirror.
- **Future agents (parked, examples):** an email-watcher agent (incl. LinkedIn job-alert
  emails), a reminder agent, a website/RSS monitor agent — each an additive `Agent` class.

---

## 8. AWS Deployment (learning-oriented)
Architecture: **Lambda** (single function; the handler dispatches scheduled vs HTTP events
into the host) + **EventBridge Scheduler** (one schedule per scheduled agent → invokes
Lambda with `{"mode":"scheduled","agent":"brief"}` in my timezone) + **Lambda Function URL**
(public HTTPS endpoint for the Telegram webhook) + **DynamoDB** (one table, partition key
`pk = "<agent>#<chat_id>"`, for memory/prefs/seen/runs) + **IAM** (execution role) +
**Secrets** (bot token & OpenRouter key as Lambda env vars, later Secrets Manager).

The full runbook (`infra/aws-runbook.md`, written during implementation) gives click-by-click
steps for: creating the IAM role, packaging Python deps (zip or container image), creating
the Lambda, adding the Function URL, registering the Telegram webhook with the secret token,
creating the DynamoDB table, and creating EventBridge schedules — plus how to verify each and
read CloudWatch logs when something fails.

Cost: daily invocations + one user's webhook traffic sit comfortably in the AWS always-free
tier; DynamoDB on-demand at this volume is effectively free.

---

## 9. Error Handling & Security
- Per-source and per-agent `try/except`: a broken feed or a failing agent never takes down
  the host or other agents.
- LLM: retry + backoff; OpenRouter fallback model chain.
- Telegram: honor `429 retry_after`.
- Webhook authenticity: verify Telegram `secret_token` header; reject otherwise.
- Secrets never in git; `.gitignore` covers `.env`.
- OpenRouter privacy: prefer paid model IDs (not `:free`, which may train on prompts);
  content is public news so low sensitivity regardless.

---

## 10. Testing
- `Agent`: unit-test `run_scheduled` / `handle_message` with a fake `Services`.
- `Source`: fixtures. `Composer`: stubbed LLM asserts structure + HTML escaping.
- `Channel`: dry-run builds payloads without sending.
- `Store`: SQLite round-trip incl. namespacing.
- `Host`: routing tests (command vs default agent).
- One real end-to-end local send behind an env flag. pytest throughout.

---

## 11. Proposed Project Layout
```
daily-brief-agent/
  README.md
  pyproject.toml            # or requirements.txt
  .env.example
  .gitignore
  src/agent_host/
    config.py  models.py  llm.py  services.py  host.py  registry.py
    channels/  {base.py, telegram.py}
    store/     {base.py, sqlite_store.py, dynamo_store.py}
    agents/
      base.py               # Agent protocol + InboundMessage
      brief/  {agent.py, sources/{base.py, placeholder.py, rss.py}, composer.py}
      chat/   {agent.py}
    entrypoints/ {local_run.py, lambda_handler.py}
  tests/
  infra/
    aws-runbook.md          # click-by-click AWS steps (learning)
    template.yaml           # optional SAM/CloudFormation, later
```

---

## 12. Open Questions (non-blocking)
- Exact daily push time & timezone (default proposal: 08:00 Asia/Shanghai) — confirm at build.
- Project name / package name: `daily-brief-agent` / `agent_host` (placeholders; rename freely).
- Brief/conversation output language: default Chinese (DeepSeek/Qwen excel at it), configurable.
