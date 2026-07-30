# Stock Agent — Phase 04: Deploy to AWS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register `StockAgent` in the host, repackage the Lambda with the native-extension (pandas/numpy/curl_cffi) wheel flags, ship it to the `agent-host` function in `ca-central-1`, wire a new EventBridge schedule (`cron(0 16 ? * MON-FRI *)` America/Vancouver), and verify the recap end-to-end.
**Architecture:** `StockAgent` is a no-arg `Agent` subclass built by `registry._agent_factories()` and filtered into the host by `config.enabled_agents`. The Lambda runs one zip whose contents sit flat at `/var/task`; a second EventBridge schedule invokes the same function with `{"mode":"scheduled","agent":"stock"}`. Secrets and `STOCK_*` overrides live in the Lambda console environment, never in git.
**Tech Stack:** AWS Lambda (Python 3.12, x86_64), DynamoDB (`agent_host`), EventBridge Scheduler, AWS CLI, `pip` manylinux wheels, yfinance + Finnhub free tier.

## Global Constraints

- **Python 3.12** (matches Lambda runtime), x86_64.
- **Free data stack only**: yfinance + Finnhub free tier + NASDAQ Trader symbol files. No paid APIs.
- **Local-first**: everything runs & passes tests locally (`STORE_BACKEND=sqlite`, network mocked/faked) before any cloud step.
- **Telegram-HTML only** in output: `<b>`,`<i>`,`<a href>` (no Markdown/`<ul>`/`<li>`); `html.escape` all fetched text before it enters an LLM prompt or a message.
- **Core edits are additive & backward-compatible** (models/channel/host/llm): defaults keep existing behavior green.
- **Secrets via env, never committed** (`FINNHUB_API_KEY`, etc.): `.env` locally (gitignored), Lambda console on cloud; never printed/logged/echoed.
- **LLM output is never authoritative.** For ticker ingestion, the deterministic allowlist check against the ground-truth universe is the ONLY gate; the LLM is a quarantined, tool-less extractor.
- **Command-only** agent (no free-form conversation). Max **50** tickers. Movers = **top 5 by |%| AND |%| ≥ 4%** (configurable via env).
- **Crypto is NOT supported** — rejected by the allowlist; recognizable crypto (e.g. `BTC-USD`) gets an explicit "crypto not supported" reason; never added to the curated allowlist / classification / data sources.
- **Per-source `try/except` + network timeouts** (Lambda hard-stops at 60s); one dead source must not kill the digest.
- Push **4pm America/Vancouver, MON-FRI**, with in-code holiday gating (XNYS); skip entirely on holidays/weekends (no message at all).
- Deployed target: region `ca-central-1`, function `agent-host`, table `agent_host`.

---

## File Structure

Files created/modified in THIS phase:

- `src/agent_host/registry.py` — **modify**: add `"stock": StockAgent` to `_agent_factories()`.
- `.env` — **modify (local, gitignored)**: add `stock` to `ENABLED_AGENTS`, add `FINNHUB_API_KEY`, `VISION_MODEL`, `STOCK_*`.
- `.env.example` — **modify (committed)**: document the new keys with placeholder values (never real secrets).
- `tests/test_registry.py` — **modify**: add a test asserting `build_host` includes the `stock` agent when enabled.
- `infra/aws-runbook.zh.md` — **modify**: append a "第 11 节 — StockAgent 部署增量" section documenting the repackage / env-var / new-schedule steps (deploy runbook is the source of truth per the task).

No files under `src/agent_host/agents/stock/` are created here — Phases 01–03 built them. This phase only registers, packages, and deploys.

---

### Task 1: Register StockAgent in the factory and enable it locally

**Files:**
- Modify: `src/agent_host/registry.py`
- Modify: `.env` (local, gitignored) and `.env.example` (committed)
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: `registry._agent_factories()->dict`, `registry.build_agents(config)->list`, `registry.build_host(config, dry_run=False)->Host` (existing); `StockAgent` (Phase 04 target: `name="stock"`, no-arg constructor, from `agent_host.agents.stock.agent`); `Config.enabled_agents:list[str]`, `Config.default_agent:str`.
- Produces: `registry._agent_factories()` now maps `"stock" -> StockAgent`; `build_agents` returns a `StockAgent` instance whenever `"stock"` is in `config.enabled_agents`. Later steps (the EventBridge schedule) rely on `run_scheduled("stock")` resolving to this agent.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_registry.py`:

```python
def test_stock_factory_is_registered():
    factories = registry._agent_factories()
    assert "stock" in factories
    # no-arg constructable, like every other factory entry
    agent = factories["stock"]()
    assert agent.name == "stock"


class StockEnabledConfig:
    telegram_bot_token = "t"
    telegram_chat_id = "42"
    telegram_webhook_secret = ""
    openrouter_api_key = "k"
    llm_model = "deepseek/deepseek-v3.2"
    llm_fallback_models = []
    store_backend = "sqlite"
    enabled_agents = ["stock", "chat"]
    default_agent = "chat"

    def __init__(self, sqlite_path):
        self.sqlite_path = sqlite_path


def test_build_host_includes_stock_when_enabled(tmp_path):
    cfg = StockEnabledConfig(sqlite_path=str(tmp_path / "agent_host.sqlite"))
    host = registry.build_host(cfg)
    names = {a.name for a in host._agents.values()}
    assert "stock" in names
    # command routing wired: /tickers resolves to the stock agent
    assert host._commands["/tickers"].name == "stock"
```

- [ ] **Step 2: Run it, expect FAIL**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL — `test_stock_factory_is_registered` fails with `KeyError: 'stock'` (the factory dict has no `"stock"` key yet).

- [ ] **Step 3: Minimal implementation**

Edit `src/agent_host/registry.py`, adding the lazy import and the map entry inside `_agent_factories()`:

```python
def _agent_factories() -> dict:
    # lazy imports so Host tests don't require the concrete agents to exist yet
    from agent_host.agents.brief.agent import BriefAgent
    from agent_host.agents.chat.agent import ChatAgent
    from agent_host.agents.stock.agent import StockAgent
    return {"brief": BriefAgent, "chat": ChatAgent, "stock": StockAgent}
```

Then enable it locally. Edit `.env` (gitignored — never committed) so the line reads:

```dotenv
ENABLED_AGENTS=brief, chat, stock
FINNHUB_API_KEY=<your-finnhub-free-key>
VISION_MODEL=google/gemini-2.5-flash
STOCK_MAX_TICKERS=50
STOCK_MOVER_THRESHOLD_PCT=4.0
STOCK_MAX_MOVERS=5
STOCK_PEER_LIMIT=5
STOCK_SCHEDULE_TZ=America/Vancouver
IMAGE_AGENT=stock
```

And document the same keys in `.env.example` (committed) with **placeholder** values only (no real secret):

```dotenv
# StockAgent (Phase 04)
FINNHUB_API_KEY=            # Finnhub free-tier key; empty => yfinance-only news, no peers
VISION_MODEL=google/gemini-2.5-flash   # cheap vision-capable OpenRouter model for screenshot import
STOCK_MAX_TICKERS=50
STOCK_MOVER_THRESHOLD_PCT=4.0
STOCK_MAX_MOVERS=5
STOCK_PEER_LIMIT=5
STOCK_SCHEDULE_TZ=America/Vancouver
IMAGE_AGENT=stock
# remember to add `stock` to ENABLED_AGENTS
```

- [ ] **Step 4: Run it, expect PASS**

Run: `pytest tests/test_registry.py -v`
Expected: PASS — `test_stock_factory_is_registered`, `test_build_host_includes_stock_when_enabled`, and the pre-existing `test_build_host_raises_on_misconfigured_default_agent` all pass (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/agent_host/registry.py tests/test_registry.py .env.example
git commit -m "feat(stock): register StockAgent in host factory + enable via config

Adds \"stock\": StockAgent to registry._agent_factories() and documents the
new FINNHUB_API_KEY / VISION_MODEL / STOCK_* env keys in .env.example.
build_host now includes the stock agent whenever it is in ENABLED_AGENTS.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

Note: `.env` is intentionally **not** staged (it is gitignored and holds the real `FINNHUB_API_KEY`). Confirm with `git status --porcelain .env` — it must print nothing that stages the file.

---

### Task 2: Local green gate — full suite passes on SQLite before any cloud step

**Files:**
- Test: entire `tests/` suite (no new file; this is the discipline gate mandated by the Global Constraints: "local-first").

**Interfaces:**
- Consumes: every module built in Phases 01–04 plus the host/channel edits from Phase 03; `STORE_BACKEND=sqlite`.
- Produces: a proven-green local build. Nothing downstream (packaging, deploy) may start until this passes — a red suite here means the zip we are about to upload is already broken.

- [ ] **Step 1: Write the failing test** — no new test; this task *runs the existing suite as the gate*. The "failing" state is a red suite (or an import error), which blocks the phase.

- [ ] **Step 2: Run it, expect FAIL (only if something is broken)**

Run: `STORE_BACKEND=sqlite python -m pytest -q`
Expected on a broken build: FAIL — e.g. `ModuleNotFoundError: No module named 'agent_host.agents.stock.agent'` (Phase 04 factory import references a package Phases 01–03 must have delivered), or a red stock test. If red, STOP and fix in the owning phase before continuing — do not package a red build.

- [ ] **Step 3: Minimal implementation** — ensure the stock package is importable and the config parses. Sanity-check the import chain the Lambda will exercise:

```bash
STORE_BACKEND=sqlite python -c "
from agent_host import registry
f = registry._agent_factories()
assert set(f) == {'brief', 'chat', 'stock'}, f
a = f['stock']()
print('stock agent:', a.name, 'commands:', a.commands)
"
```

Expected: prints `stock agent: stock commands: ['/tickers', '/add', '/remove', '/reset', '/help', '/confirm', '/cancel']` with no traceback.

- [ ] **Step 4: Run it, expect PASS**

Run: `STORE_BACKEND=sqlite python -m pytest -q`
Expected: PASS — all tests green (line ends `N passed` with `0 failed`). Capture the count; this is the evidence the local build is shippable.

- [ ] **Step 5: Commit** — nothing to commit (verification-only task). Record the green result in the PR/session notes. If any fix was required, it belongs to the phase that owns the failing module, committed there.

---

### Task 3: De-risk native-ext packaging and build the deploy artifact

**Files:**
- No source files. This task produces the on-disk artifact `function.zip` (and optionally `stock-native-layer.zip`) at repo root; both are gitignored build outputs.

**Interfaces:**
- Consumes: `pyproject.toml` dependencies (now including yfinance → pandas + numpy + curl_cffi), Python 3.12, the Lambda handler at `agent_host/entrypoints/lambda_handler.py`.
- Produces: `function.zip` with `agent_host/` and every dependency **flat at the top level** (no `build/` or `src/` prefix), built with Lambda-compatible manylinux x86_64 wheels. If the fat zip exceeds Lambda's limits, a separate `stock-native-layer.zip` layer artifact plus the attach step.

> **⚠️ Apple-Silicon / x86_64 reminder (read before running):** `pip install` on macOS (especially Apple Silicon) or Windows grabs `macosx_*` / `win_*` wheels for the native packages (`pandas`, `numpy`, `curl_cffi`, `pydantic_core`, `jiter`). Those crash Lambda's Amazon-Linux x86_64 runtime at import (`invalid ELF header` / `No module named '..._pydantic_core'`). You MUST pass the four wheel flags below so pip downloads manylinux x86_64 CPython-3.12 wheels regardless of your machine. This must match the Lambda **Architecture = x86_64** setting.

- [ ] **Step 1: De-risk first — throwaway "import yfinance" smoke test on Lambda**

This is the single biggest deployment risk (spec §15.1). Before rebuilding the real zip, prove the native stack imports on Lambda. Build a minimal probe zip:

```bash
cd /Users/feiren/Documents/CS学习/GitHub项目/daily-brief-agent
rm -rf probe probe.zip
pip install yfinance \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  -t probe/
cat > probe/probe_handler.py <<'PY'
def handler(event, context=None):
    import yfinance, pandas, numpy, curl_cffi
    return {"ok": True, "yf": yfinance.__version__,
            "pandas": pandas.__version__, "numpy": numpy.__version__}
PY
cd probe && zip -rq ../probe.zip . && cd ..
ls -lh probe.zip
```

Expected: `pip` ends with `Successfully installed ... yfinance-...`; `probe.zip` prints a size (typically **40–60 MB** — pandas+numpy dominate). Note the size — it decides fat-zip vs layer in Step 3.

- [ ] **Step 2: Run it, expect the probe to import cleanly on Lambda**

Create a throwaway function from the probe zip, invoke it, then delete it:

```bash
ROLE_ARN=$(aws iam get-role --role-name agent-host-lambda-role --query "Role.Arn" --output text)
aws lambda create-function \
  --function-name agent-host-probe \
  --runtime python3.12 --architectures x86_64 \
  --handler probe_handler.handler \
  --role "$ROLE_ARN" \
  --timeout 30 --memory-size 512 \
  --zip-file fileb://probe.zip \
  --region ca-central-1
aws lambda invoke \
  --function-name agent-host-probe \
  --payload '{}' --cli-binary-format raw-in-base64-out \
  --region ca-central-1 \
  probe_response.json
cat probe_response.json
aws lambda delete-function --function-name agent-host-probe --region ca-central-1
rm -rf probe probe.zip probe_response.json
```

Expected: `probe_response.json` contains `{"ok": true, "yf": "...", "pandas": "...", "numpy": "..."}` and NOT an `errorMessage` with `invalid ELF header` / `No module named ...`. A clean JSON here proves the wheel flags produced Lambda-runnable binaries. If it errors, STOP — re-run Step 1 with the four flags (the most common cause is wheels built for your local OS).

> If `create-function` reports `The role defined for the function cannot be assumed by Lambda` or `role ... not found`, the `agent-host-lambda-role` from the runbook §3 is missing or IAM has not propagated — wait 10–15 s and retry, or create the role per runbook §3 first.

- [ ] **Step 3: Build the real deploy artifact (fat zip; fall back to layer if oversized)**

Rebuild `function.zip` from the whole project with the wheel flags:

```bash
cd /Users/feiren/Documents/CS学习/GitHub项目/daily-brief-agent
rm -rf build function.zip
pip install . \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  -t build/
# optional slim: boto3/botocore ship in the Lambda runtime already
rm -rf build/boto3 build/botocore build/s3transfer build/dateutil \
       build/python_dateutil* build/jmespath build/urllib3
cd build && zip -rq ../function.zip . && cd ..
ls -lh function.zip
unzip -l function.zip | grep "agent_host/entrypoints/lambda_handler.py"
unzip -l function.zip | grep -E "^\s+[0-9].*(yfinance|pandas|numpy|curl_cffi)/" | head
```

Expected: `pip` ends with `Successfully installed ... agent-host-0.1.0` (a dependency-resolver warning about *other* local packages is harmless); the handler grep prints one line; the last grep shows top-level `pandas/`, `numpy/`, `yfinance/`, `curl_cffi/` entries (flat, no `build/` prefix).

**Decision — fat zip vs layer, based on the `ls -lh function.zip` size:**
- **≤ 50 MB** → fat zip is fine; go straight to Task 4 with `--zip-file fileb://function.zip`.
- **> 50 MB (direct upload) but ≤ 250 MB unzipped** → upload the zip via S3 in Task 4 (Step notes there), OR split native deps into a layer (below).
- **Phase 0 decided "layer" OR the zip is too big** → build a numpy/pandas/curl_cffi **layer** and keep the function zip lean:

```bash
# Layer: native deps only, under python/ (Lambda's layer convention)
rm -rf layer stock-native-layer.zip
pip install numpy pandas curl_cffi \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  -t layer/python/
cd layer && zip -rq ../stock-native-layer.zip . && cd ..
ls -lh stock-native-layer.zip

PUBLISH=$(aws lambda publish-layer-version \
  --layer-name agent-host-native \
  --description "numpy+pandas+curl_cffi for yfinance (py3.12 x86_64)" \
  --compatible-runtimes python3.12 \
  --compatible-architectures x86_64 \
  --zip-file fileb://stock-native-layer.zip \
  --region ca-central-1)
echo "$PUBLISH" | python3 -c "import sys,json; print(json.load(sys.stdin)['LayerVersionArn'])"
```

If you build the layer, then rebuild `function.zip` **without** those three packages (so they are not shipped twice):

```bash
rm -rf build/numpy build/pandas build/curl_cffi
cd build && rm -f ../function.zip && zip -rq ../function.zip . && cd ..
ls -lh function.zip   # expect a much smaller zip (single-digit MB)
```

Record the `LayerVersionArn` — Task 4 attaches it with `--layers`.

- [ ] **Step 4: Verify the artifact layout**

Run: `unzip -l function.zip | head -20`
Expected: top-level entries `agent_host/`, `openai/`, `httpx/`, `pydantic_core/`, and (fat-zip path) `yfinance/`, `pandas/`, `numpy/`, `curl_cffi/` — with **no** shared `build/` or `src/` prefix. If everything is nested under `build/...`, you zipped from the repo root instead of from inside `build/`; redo the `cd build && zip ...` step.

- [ ] **Step 5: Commit** — the zip/layer are build artifacts, not source; do not commit them. Confirm they are ignored:

```bash
git status --porcelain | grep -E "function.zip|stock-native-layer.zip|build/|layer/" || echo "clean: artifacts ignored"
```

Expected: prints `clean: artifacts ignored`. If any artifact shows as untracked, add it to `.gitignore` and commit only the `.gitignore` change:

```bash
git add .gitignore
git commit -m "chore(stock): gitignore native-ext build artifacts (function.zip, layer)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Deploy code and set Lambda environment variables

**Files:**
- No source files. Cloud-state change: updates the `agent-host` function code and its environment in `ca-central-1`.

**Interfaces:**
- Consumes: `function.zip` (and optionally the `LayerVersionArn`) from Task 3; the existing `agent-host` function, handler `agent_host.entrypoints.lambda_handler.lambda_handler`, and its already-set env vars from the runbook (`TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, `STORE_BACKEND=dynamo`, etc.).
- Produces: a deployed function that includes the `stock` agent, with `FINNHUB_API_KEY`, `VISION_MODEL`, `STOCK_*`, `IMAGE_AGENT`, and `stock` in `ENABLED_AGENTS` live in the Lambda environment.

- [ ] **Step 1: Push the new code**

```bash
aws lambda update-function-code \
  --function-name agent-host \
  --zip-file fileb://function.zip \
  --region ca-central-1
```

If Task 3 chose the **layer** path, also attach it (this replaces the function's layer list — pass every layer you want, here just the one):

```bash
aws lambda update-function-configuration \
  --function-name agent-host \
  --layers <LayerVersionArn-from-Task-3> \
  --region ca-central-1
```

If the zip is > 50 MB (S3 path), upload to a bucket first, then `--s3-bucket/--s3-key` instead of `--zip-file`.

Expected: JSON describing the function; watch `"LastUpdateStatus"` — it starts `"InProgress"`.

- [ ] **Step 2: Wait for the update to settle, expect Successful**

```bash
aws lambda wait function-updated \
  --function-name agent-host --region ca-central-1
aws lambda get-function-configuration \
  --function-name agent-host --region ca-central-1 \
  --query "{State:State,Last:LastUpdateStatus,Runtime:Runtime,Arch:Architectures,CodeSize:CodeSize}"
```

Expected: `{"State": "Active", "Last": "Successful", "Runtime": "python3.12", "Arch": ["x86_64"], "CodeSize": <bytes>}`. `Arch` MUST be `x86_64` to match the manylinux wheels.

- [ ] **Step 3: Set env vars via the CONSOLE (not a whole-env CLI replace)**

> **Why console, not `aws lambda update-function-configuration --environment`:** the CLI `--environment` flag **replaces the entire `Variables` map**, silently wiping `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, and every other secret already set per the runbook. Add keys through the console so existing values stay intact.

Console → Lambda → `agent-host` (region `ca-central-1`) → **Configuration** → **Environment variables** → **Edit** → **Add environment variable**, adding each row below, then edit the existing `ENABLED_AGENTS` to append `stock`:

| Key | Value |
|---|---|
| `FINNHUB_API_KEY` | your Finnhub free-tier key (the real secret; never commit/echo) |
| `VISION_MODEL` | `google/gemini-2.5-flash` (cheap vision-capable OpenRouter id) |
| `STOCK_MAX_TICKERS` | `50` (omit to use the code default) |
| `STOCK_MOVER_THRESHOLD_PCT` | `4.0` (omit to use the default) |
| `STOCK_MAX_MOVERS` | `5` (omit to use the default) |
| `STOCK_PEER_LIMIT` | `5` (omit to use the default) |
| `STOCK_SCHEDULE_TZ` | `America/Vancouver` (doc-only; real schedule is in EventBridge) |
| `IMAGE_AGENT` | `stock` |
| `ENABLED_AGENTS` | `brief, chat, stock` (**edit the existing row** — append `stock`) |

Click **Save**.

> Graceful-degradation note: `FINNHUB_API_KEY` empty is a valid config — the Finnhub source returns `[]` and yfinance `.news` is the baseline (no peers). Only leave it empty deliberately; the recommended setup is to set the key.

- [ ] **Step 4: Verify the environment took (values are masked; check keys + non-secret values)**

```bash
aws lambda get-function-configuration \
  --function-name agent-host --region ca-central-1 \
  --query "Environment.Variables | keys(@)"
aws lambda get-function-configuration \
  --function-name agent-host --region ca-central-1 \
  --query "Environment.Variables.{Enabled:ENABLED_AGENTS,Image:IMAGE_AGENT,Vision:VISION_MODEL,Store:STORE_BACKEND}"
```

Expected: the `keys(@)` list contains `FINNHUB_API_KEY`, `VISION_MODEL`, `IMAGE_AGENT`, `ENABLED_AGENTS`, and the pre-existing keys (nothing was wiped). The second query prints `{"Enabled": "brief, chat, stock", "Image": "stock", "Vision": "google/gemini-2.5-flash", "Store": "dynamo"}`. (`STORE_BACKEND` MUST still be `dynamo` — SQLite would lose all state between invocations.)

- [ ] **Step 5: Commit** — no repo change (cloud-state only). Record the deployed `CodeSize` and confirmed env keys in the session/PR notes.

---

### Task 5: Create the EventBridge schedule `agent-host-stock-recap`

**Files:**
- No source files. Cloud-state change: a new EventBridge Scheduler schedule in `ca-central-1`.

**Interfaces:**
- Consumes: the deployed `agent-host` function (its ARN); the scheduled payload contract `{"mode":"scheduled","agent":"stock"}` that `lambda_handler` routes to `host.run_scheduled("stock")`.
- Produces: schedule `agent-host-stock-recap`, `cron(0 16 ? * MON-FRI *)` in `America/Vancouver`, State `ENABLED`, targeting `agent-host` with its own invoke role. This is what triggers the recap every trading weekday at 16:00 PT (holiday gating is in `calendar.py`, not here).

- [ ] **Step 1: Create the schedule via the CONSOLE**

Console → EventBridge → **Scheduler** → **Schedules** → **Create schedule**:
1. **Schedule name:** `agent-host-stock-recap`.
2. **Schedule pattern:** **Recurring schedule** → **Cron-based schedule**.
3. **Cron expression:** enter `0 16 ? * MON-FRI *` (console wraps it as `cron(0 16 ? * MON-FRI *)`). Six EventBridge fields — minute `0`, hour `16`, day-of-month `?`, month `*`, day-of-week `MON-FRI`, year `*`: "16:00, Mon–Fri, any month/year." (`?` on day-of-month because day-of-week is set; EventBridge forbids both being `*`.)
4. **Timezone:** `America/Vancouver` (interprets the cron; handles PST/PDT so it is always 16:00 local).
5. **Flexible time window:** **Off** (fire exactly at 16:00).
6. **Next.**
7. **Target:** **AWS Lambda** → **Invoke** → **Lambda function:** `agent-host` (region `ca-central-1`).
8. **Additional settings** → **Input** → **Constant JSON text** → paste exactly:
   ```json
   {"mode": "scheduled", "agent": "stock"}
   ```
9. **Execution role:** **Create new role for this schedule** (Scheduler auto-creates a minimal role that can `lambda:InvokeFunction` on `agent-host` only). This is a *different* role from `agent-host-lambda-role` and from the brief schedule's role — it authorizes the Scheduler service to invoke, independent of what the function's own role permits.
10. **Next**, keep retry/DLQ and schedule group `default` as-is, **Next**, review, **Create schedule**.

> This is a **second, independent** schedule alongside the brief's `agent-host-daily-brief`; do not edit that one. Same 4pm clock time, different payload (`agent: "stock"`), different target-invoke role.

- [ ] **Step 2: Verify the schedule exists and is correct**

```bash
aws scheduler get-schedule \
  --name agent-host-stock-recap --region ca-central-1 \
  --query "{State:State,Cron:ScheduleExpression,Tz:ScheduleExpressionTimezone,TargetArn:Target.Arn,Input:Target.Input,Role:Target.RoleArn}"
```

Expected:
```json
{
  "State": "ENABLED",
  "Cron": "cron(0 16 ? * MON-FRI *)",
  "Tz": "America/Vancouver",
  "TargetArn": "arn:aws:lambda:ca-central-1:<ACCOUNT_ID>:function:agent-host",
  "Input": "{\"mode\": \"scheduled\", \"agent\": \"stock\"}",
  "Role": "arn:aws:iam::<ACCOUNT_ID>:role/service-role/Amazon_EventBridge_Scheduler_LAMBDA_<suffix>"
}
```
Confirm `Cron`, `Tz`, and `Input` match exactly. A wrong `Input` (e.g. `agent: "brief"`) would fire the wrong agent.

- [ ] **Step 3: Confirm the Scheduler role may invoke the function** — the console auto-role grants this, but verify the target ARN resolves and the schedule is not `DISABLED`:

```bash
aws scheduler list-schedules \
  --name-prefix agent-host --region ca-central-1 \
  --query "Schedules[].{Name:Name,State:State}"
```

Expected: both `agent-host-daily-brief` and `agent-host-stock-recap` listed, each `State: "ENABLED"`.

- [ ] **Step 4: (Optional) prove the wiring without waiting for 16:00** — temporarily set the cron a few minutes ahead, confirm a Telegram recap arrives (or a holiday/weekend no-op), then restore `cron(0 16 ? * MON-FRI *)`. This is a live-fire check; skip it if Task 6's manual invoke already proved the chain and you only need schedule *wiring* confidence.

- [ ] **Step 5: Commit** — no repo change (cloud-state only). Record the confirmed `get-schedule` output in the session/PR notes.

---

### Task 6: End-to-end verification and holiday-gate check

**Files:**
- No source files. Runtime verification of the deployed function.

**Interfaces:**
- Consumes: the deployed function + schedule; the webhook path (`/tickers` command via the bot) and the scheduled path (`{"mode":"scheduled","agent":"stock"}`).
- Produces: evidence the recap works end-to-end. Key nuance: `Host.run_scheduled` **swallows agent exceptions into logs and still returns `{"statusCode":200}`** — so a `200` is NOT proof of success; CloudWatch logs are.

- [ ] **Step 1: Verify the webhook/command path — `/tickers` via the bot**

In Telegram, send `/tickers` to the bot. Expected: within a few seconds, a reply listing the current pool, or "empty → tracking the market by default" text (per spec §5). Then send `/help`. Expected: the command list including image-import explanation. A silent non-response means routing/handler failure — go to Step 4 (logs) before assuming success.

- [ ] **Step 2: Verify the scheduled path — manual invoke with the schedule's payload**

```bash
aws lambda invoke \
  --function-name agent-host \
  --payload '{"mode":"scheduled","agent":"stock"}' \
  --cli-binary-format raw-in-base64-out \
  --region ca-central-1 \
  response.json
cat response.json
```

Expected: `response.json` contains `{"statusCode": 200, "body": "ok"}`.

> **⚠️ 200 is necessary but NOT sufficient.** `host.run_scheduled` catches every agent exception (`host.py` lines 25–28: `log.exception(...)`) and returns anyway, so the HTTP layer reports `200` even when the recap silently failed inside `run_scheduled`. You MUST read the logs (Step 4) to confirm it actually built and sent — do not stop at the `200`.

- [ ] **Step 3: Confirm the Telegram recap actually arrived** — check the bot chat. On a **trading day**, expect a recap message (indices + movers/why/news/earnings, sections omitted when empty). On a **holiday or weekend**, expect **no message at all** (the `calendar.py` XNYS gate returns early — this is correct behavior, not a failure). If it is a non-trading day, Step 4's logs should show the early-return, and no send.

- [ ] **Step 4: Tail CloudWatch logs — the real success signal**

```bash
aws logs tail /aws/lambda/agent-host \
  --since 10m --region ca-central-1 --follow
```

Re-run the invoke from Step 2 in another terminal while this tails. Expected on a trading day: log lines for the stock run and a final `REPORT RequestId: ...` with `Duration` well under 60000 ms. Expected on a non-trading day: a line indicating the holiday/weekend early-return, then `REPORT`, and no send.

**A swallowed agent failure looks like this** (read bottom-up — last line is the error type/message):
```
[ERROR] agent stock run_scheduled failed
Traceback (most recent call last):
  File "/var/task/agent_host/host.py", line 26, in run_scheduled
    agent.run_scheduled(self._svc_for(agent))
  File "/var/task/agent_host/agents/stock/agent.py", line ..., in run_scheduled
    ...
SomeException: description of what went wrong
```
If you see `agent stock run_scheduled failed`, the `200` was hollow — fix the underlying error (per-source `try/except` should have contained data-source failures; a top-level traceback usually means a config/import/logic bug), redeploy (Task 3→4), and re-verify.

- [ ] **Step 5: Commit** — no repo change (verification-only). Record in the session/PR notes: `/tickers` reply confirmed, manual invoke `200`, logs clean (or holiday early-return observed), recap delivered. This closes Phase 04.

---

## Deploy checklist (quick reference)

1. Local suite green on `STORE_BACKEND=sqlite` (Task 2) — **gate; do not proceed while red.**
2. `stock` registered in `_agent_factories()` + in `ENABLED_AGENTS` (Task 1).
3. Native-ext smoke test passed; `function.zip` (or zip + layer) built with the four manylinux flags, x86_64 (Task 3).
4. `update-function-code` + `wait function-updated` → `LastUpdateStatus: Successful`; env vars added via **console** (secrets intact) (Task 4).
5. `agent-host-stock-recap` schedule `cron(0 16 ? * MON-FRI *)` America/Vancouver, payload `{"mode":"scheduled","agent":"stock"}`, its own invoke role (Task 5).
6. `/tickers` replies; manual invoke `200` **and** clean CloudWatch logs; holiday/weekend → no message (Task 6).
