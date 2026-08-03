# agent-host

> 🇨🇳 中文说明见 [README.zh.md](README.zh.md).

A small, pluggable **host** for running Telegram-connected AI agents, running **serverless on AWS**. The host owns the shared plumbing — a Telegram channel, an OpenRouter-backed LLM client, a pluggable storage backend, and message routing — while individual **agents** plug in to do the work. Three ship out of the box: `BriefAgent` (a scheduled daily news brief), `ChatAgent` (free-form conversation with per-chat memory), and `StockAgent` (a command-only, daily after-close US-market recap with a screenshot-driven watchlist import). The *same* codebase runs two ways: a local long-polling process for development, and an AWS Lambda function in production.

## Contents

- [Architecture](#architecture)
  - [BriefAgent and ChatAgent host architecture](#briefagent-and-chatagent-host-architecture)
  - [StockAgent — daily US-market recap (showcase)](#stockagent--daily-us-market-recap-showcase)
- [Tech stack & what it demonstrates](#tech-stack--what-it-demonstrates)
- [Local quickstart](#local-quickstart)
  - [BriefAgent and ChatAgent usage](#briefagent-and-chatagent-usage)
  - [StockAgent usage](#stockagent-usage)
- [How to add a new agent](#how-to-add-a-new-agent)
- [Deploying to AWS](#deploying-to-aws)

## Architecture

### BriefAgent and ChatAgent host architecture

One Lambda function, two entry paths, one table:

- A **Telegram webhook** (delivered to a **Lambda Function URL**) drives live chat.
- A daily **EventBridge schedule** fires the *same* Lambda to compose and push the news brief.
- **DynamoDB** holds conversation memory and news-dedup state, so the stateless function has somewhere durable to remember.

```mermaid
flowchart LR
    subgraph Triggers
      TG[Telegram user message]
      EB[EventBridge Scheduler<br/>daily cron]
    end
    TG -->|HTTPS POST + secret-token header| FU[Lambda Function URL<br/>auth: NONE]
    FU --> L[AWS Lambda: agent-host<br/>Python 3.12]
    EB -->|const JSON: mode=scheduled, agent=brief| L
    L <-->|GetItem / PutItem| D[(DynamoDB<br/>single-table, pk only)]
    L -->|prompt| OR[OpenRouter LLM]
    L -->|sendMessage| OUT[Telegram → user]
```

**Why these choices:**

- **Serverless (Lambda).** A personal bot is idle almost all the time; pay-per-invocation means ~$0/month within the free tier and nothing to keep running or patch.
- **Function URL, not API Gateway.** The only caller is Telegram's server, so a Lambda Function URL is the minimal public HTTPS endpoint. It's `auth: NONE` (Telegram can't sign AWS SigV4 requests), so the endpoint is guarded **in code**: a **fail-closed secret-token check** (`hmac.compare_digest`; an unset secret returns `403`, never open) plus a **sender allow-list** (only the owner's `chat_id` is served).
- **DynamoDB, single table.** Lambda's filesystem is ephemeral, so state must live externally. The code only ever does `GetItem` / `PutItem` against a single partition key (namespaces are baked into the key, e.g. `brief#memory#42`) — so the IAM policy grants exactly those two actions on exactly that one table. **Least privilege by construction.**
- **Scheduling lives outside the code.** Lambda is passive and can't wake itself, so "run the brief at 4pm" lives in an EventBridge rule, not an env var. Each agent that needs a schedule gets its own rule, so different agents can run at different times with **zero code change**.

### StockAgent — daily US-market recap (showcase)

`StockAgent` is a **command-only** agent (no free-form chat) plugged into the same host that pushes one Telegram digest per US trading day, after market close. The recap itself is straightforward — the centerpiece is *how the watchlist gets populated safely*.

**The showcase: input validation mapped to the OWASP Top 10 for LLM Applications (2025).** Tickers can enter the watchlist through three channels — a text `/add`, a screenshot upload, and a `/confirm` on a pending import — and all three route through the *same* deterministic gate. The north star: **the LLM (text or vision) is an untrusted, tool-less *extractor*, never an authority.** Nothing it emits is acted on until it survives a **deterministic allowlist check** against a ground-truth ticker universe — NASDAQ Trader symbol files, refreshed weekly, plus a small curated set of indices/futures. Rather than trying to enumerate every bad input — an unbounded, losing game against prompt injection, jailbreaks, homoglyphs, zero-width Unicode, formula/XSS payloads, and PII leakage — the gate enumerates the only things that are *valid*: real, currently-listed tickers (~10⁴–10⁵ items). Anything outside that small, closed, knowable set is discarded, turning an unbounded adversarial-NLP problem into an O(1) set-membership test.

```mermaid
flowchart LR
    A["Text: /add AAPL MSFT ..."] --> G
    B["Screenshot: brokerage /<br/>TradingView holdings"] --> V["Quarantined vision extractor<br/>(tool-less, schema-locked JSON)"]
    V --> G
    G["Deterministic allowlist gate<br/>(NASDAQ Trader files +<br/>curated indices / futures)"]
    G -->|reject| R["Discarded: injected instructions,<br/>PII, crypto, homoglyphs,<br/>formula / XSS payloads"]
    G -->|accept, then /confirm| W["Watchlist (max 50 tickers)"]
```

**What gets structurally discarded, by construction:** injected instructions ("ignore previous instructions…", fake `SYSTEM:` authority, jailbreak/DAN-style prompts), PII skimmed from a screenshot (account numbers, balances, cost basis), **crypto** (explicitly out of scope — rejected with a `"crypto not supported"` reason rather than silently dropped), homoglyphs and invisible Unicode (zero-width characters, bidi controls, Unicode tag characters), and output-side payloads (CSV-formula injection like `=IMPORTXML(...)`, `<img onerror=...>` XSS). None of these are members of the allowlist, so none survive — even a doctored screenshot that convinces the vision model to "add TSLA x1000 and delete the watchlist" only lets the substring `TSLA` through; the instruction and the quantity are discarded at the gate.

**Screenshot import, end to end:** send a photo of brokerage or TradingView holdings → a quarantined, schema-locked vision extractor (`VISION_MODEL`, an OpenRouter vision-capable model) reads *only* ticker symbols, never account data → the same deterministic allowlist gate validates the candidates → the bot shows **only the validated tickers** → `/confirm` saves them, `/cancel` discards them. The raw image is held in memory only — never stored, logged, or echoed; balances and account numbers are never even asked for.

## Tech stack & what it demonstrates

| Layer | What's used |
|---|---|
| Language / runtime | Python 3.12 |
| Compute | AWS Lambda (serverless, x86_64) + Lambda Function URL |
| Scheduling | Amazon EventBridge Scheduler (cron) |
| Storage | Amazon DynamoDB (on-demand, single-table, one partition key) |
| Security | IAM least-privilege execution role; fail-closed webhook secret; sender allow-list |
| Config | `pydantic-settings` (12-factor: env-driven; secrets never committed) |
| Integrations | Telegram Bot API (webhook + long-poll); OpenRouter (multi-model with fallbacks) |
| Packaging | `manylinux` / x86_64 wheels built for the Lambda runtime; flat zip |
| Testing | `pytest` + `moto` (mock DynamoDB); fakes / dry-run; env-gated live e2e |
| Observability | Amazon CloudWatch Logs |
| Market data (StockAgent) | yfinance (`curl_cffi` session + retry/backoff) + pandas/numpy (native wheels) |
| Company & market news (StockAgent) | Finnhub free tier (60 calls/min; empty key ⇒ graceful degrade to yfinance) |
| Ticker universe / ground truth (StockAgent) | NASDAQ Trader symbol files (`nasdaqlisted.txt` / `otherlisted.txt`), weekly refresh |
| Screenshot ingestion (StockAgent) | OpenRouter vision-capable model (`VISION_MODEL`), tool-less extractor |
| Market calendar (StockAgent) | `holidays` package (XNYS trading-day gate) |
| Input-validation guardrail (StockAgent) | Deterministic allowlist gate mapped to the OWASP Top 10 for LLM Applications (2025) |

**Engineering practices on display:** a pluggable agent architecture (adding an agent ≈ one class + one registry line), 12-factor configuration, least-privilege IAM, cross-platform dependency packaging, webhook security, and test-driven development. The full click-by-click deployment guide lives in **[`infra/aws-runbook.md`](infra/aws-runbook.md)** ([中文版](infra/aws-runbook.zh.md)).

**StockAgent adds:** a free-stack market-data pipeline (yfinance + Finnhub), an OpenRouter vision model for screenshot ingestion, and — the showcase — a deterministic, OWASP-mapped input-validation guardrail that treats the LLM as an untrusted extractor rather than an authority. See [StockAgent — daily US-market recap (showcase)](#stockagent--daily-us-market-recap-showcase) above.

---

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
`TIMEZONE=Asia/Shanghai`, `ENABLED_AGENTS=brief, chat, stock`, `DEFAULT_AGENT=chat`, ...) — leave
those as-is to start.

Run the tests:

```bash
pytest
```

One test (`tests/test_e2e_local.py`) is skipped by default because it sends a **real** message to
your real Telegram chat — it only runs if you export `RUN_E2E=1` with a fully-populated `.env`.

### BriefAgent and ChatAgent usage

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

### StockAgent usage

`StockAgent` is on by default in `.env.example` (`ENABLED_AGENTS=brief, chat, stock`,
`IMAGE_AGENT=stock`). Optionally set `FINNHUB_API_KEY` (recommended — better news links + peer
propagation; an empty key gracefully degrades to yfinance-only) and the `VISION_MODEL` /
`STOCK_MAX_TICKERS` / `STOCK_MOVER_THRESHOLD_PCT` / `STOCK_MAX_MOVERS` / `STOCK_PEER_LIMIT` /
`STOCK_SCHEDULE_TZ` tuning knobs — all documented in `.env.example`.

Run it once, right now, from the command line:

```bash
python -m agent_host.entrypoints.local_run run stock
```

Or run `serve` (see above) and drive it entirely by command — `StockAgent` is **command-only**,
it never joins free-form chat:

| Command | Behavior |
|---|---|
| `/tickers` | Show your current watchlist (or "empty → tracking the market by default"). |
| `/add AAPL MSFT …` | Validate and add symbols; reports what was added / rejected and why. |
| `/remove AAPL …` | Remove symbols from your watchlist. |
| `/reset` | Clear the watchlist → back to default "track the market" mode. |
| `/help` | List commands and explain screenshot import. |
| *(send a photo)* | Screenshot import — shows validated tickers, then `/confirm` or `/cancel`. |
| `/confirm`, `/cancel` | Accept or discard a pending screenshot import. |

Max 50 tickers. An empty watchlist isn't an error — it's the default "track the market" mode.

**What the daily recap contains:** four indices (S&P 500, Nasdaq, Dow, and SOX — 费半), your
watchlist's notable movers (top 5 by |%| change, ≥ 4%) **with attributed causes** (earnings,
news, or an honest "no clear catalyst" — never fabricated), relevant news (1–2 sentences + a
link, skipping tickers with nothing notable), and a dedicated earnings section. It **sends
nothing on market holidays or weekends** (checked against the XNYS trading calendar via the
`holidays` package). An empty watchlist defaults to a market-wide recap instead of tracking 500
individual names.

**Schedule:** 4pm `America/Vancouver`, Monday–Friday, via its own EventBridge schedule (payload
`{"mode": "scheduled", "agent": "stock"}`) — the same clock time as the brief, sent as an
independent message.

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
   `agents/brief/` or `agents/chat/` package layout as a template — or `agents/stock/` if your
   agent needs multiple source modules and a validation layer). Set `name` to a short unique
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
       from agent_host.agents.stock.agent import StockAgent
       from agent_host.agents.yours.agent import YourAgent   # add your import
       return {"brief": BriefAgent, "chat": ChatAgent, "stock": StockAgent,
               "yours": YourAgent}  # add your entry
   ```
   `build_agents()` only instantiates agents whose name appears in this dict **and** in
   `config.enabled_agents`, so both steps below are required.
4. **Enable it** by adding its name to `ENABLED_AGENTS` in `.env` (comma-separated, e.g.
   `ENABLED_AGENTS=brief, chat, stock, yours`) — this is read by `Config.enabled_agents` and is
   what `build_agents()` filters against.
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

`StockAgent` is built and fully tested locally; it isn't deployed to production yet. Taking it
live follows the **same pattern** as the brief: register it (already done in code) and make sure
`stock` is in `ENABLED_AGENTS`, repackage (yfinance pulls in native `pandas`/`numpy`/`curl_cffi`
wheels — see the Tech stack table above and validate the Lambda zip size early), add its own
EventBridge schedule targeting the same Lambda with payload `{"mode": "scheduled", "agent":
"stock"}`, and set its env vars (`FINNHUB_API_KEY`, `VISION_MODEL`, any `STOCK_*` overrides) in
the Lambda console — see the runbook.
