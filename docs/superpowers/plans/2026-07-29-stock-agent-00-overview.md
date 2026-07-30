# StockAgent Overview & Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `StockAgent`, a hardened, command-only after-close US-market recap agent plugged into the existing agent-host, curated by text and screenshot upload behind a deterministic-allowlist guardrail.

**Architecture:** A new agent package `src/agent_host/agents/stock/` subclasses the existing `Agent` base and consumes the injected `Services` bundle exactly like `BriefAgent`, plus a small set of additive, backward-compatible core changes (image field on `InboundMessage`, photo parsing + file download on `TelegramChannel`, a vision method on `LLMClient`, unknown-command/photo routing in `Host`, new `Config` fields). The load-bearing security property is that the LLM is a quarantined, tool-less extractor whose output is never acted on until it survives a deterministic allowlist check against a ground-truth ticker universe (NASDAQ Trader files + a curated non-equity set, no crypto).

**Tech Stack:** Python 3.12, pydantic-settings, yfinance (+ pandas/numpy/curl_cffi native exts), Finnhub free tier, NASDAQ Trader symbol files, `holidays` (XNYS), OpenRouter (text + vision), pytest, AWS Lambda + EventBridge (`ca-central-1`, function `agent-host`, table `agent_host`).

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

## How this plan set is organized

This is the **overview / roadmap**. It owns Phase 0 (packaging de-risk) and the whole-feature decomposition. The four implementation phases live in sibling files and MUST be executed in order:

| Phase | File | Delivers | Depends on |
|---|---|---|---|
| 0 | *this file* | Packaging de-risk spike + decision gate (fat-zip vs numpy/pandas layer) | — |
| 01 | `2026-07-29-stock-agent-01-watchlist-guardrail.md` | `universe.py`, `watchlist.py`, `classify.py`, the deterministic `validate_candidates` guardrail + full attack-catalog tests | Phase 0 decision (for `yfinance`/`.info` use in `classify`) |
| 02 | `2026-07-29-stock-agent-02-recap.md` | `calendar.py`, `sources/`, `composer.py`, `agent.py` `run_scheduled` + commands (text `/add` **reuses** Phase 01 `validate_candidates`) | Phase 01 |
| 03 | `2026-07-29-stock-agent-03-image-import.md` | Additive core edits (`InboundMessage.photo_file_ids`, `TelegramChannel.download_file`/photo parse, `LLMClient.complete_vision`, `Host` photo + unknown-command routing) + the vision extractor image-import flow (**reuses** Phase 01 `validate_candidates`) | Phase 01, Phase 02 |
| 04 | `2026-07-29-stock-agent-04-deploy.md` | Registry/config registration, EventBridge schedule, Lambda env + package per the Phase 0 gate, cloud verification | Phase 0 gate, Phases 01–03 |

**Dependency order is strict:** 01 → 02 → 03 → 04, and 02 & 03 both **reuse Phase 01's `validate_candidates`** as the single ingestion gate (text commands and image import share the exact same deterministic pipeline — no second validator is written). Each phase ends with locally-passing tests and is independently reviewable.

---

## File Structure (whole feature)

### New package `src/agent_host/agents/stock/` (Phases 01–02, mirrors `brief/`)

- `__init__.py` — package marker (empty), mirrors `brief/__init__.py`.
- `universe.py` — ground-truth ticker universe: parse NASDAQ Trader files, merge curated non-equity set (indices/futures, **no crypto**), membership + type lookup, weekly cached refresh via `Store`. **(Phase 01)**
- `watchlist.py` — Unicode sanitize + normalize + shape check + crypto detection + `validate_candidates` (the deterministic gate) + `WatchlistManager` CRUD/pending-import state on prefs. **(Phase 01)**
- `classify.py` — `TickerClass` + `classify()`: ticker → kind/sector/peers/theme for industry propagation, no crypto. **(Phase 02)**
- `calendar.py` — `is_trading_day(d)` weekday + XNYS holiday gate. **(Phase 02)**
- `composer.py` — `RecapData` + `StockComposer.compose()`: structured recap → Telegram-HTML, omit empty sections, escape all fetched text. **(Phase 02)**
- `agent.py` — `StockAgent(Agent)`: commands + `run_scheduled` (build & push recap, holiday gate) + `handle_message`. **(Phase 02, extended in 03 for images)**
- `sources/__init__.py` — package marker (empty). **(Phase 02)**
- `sources/base.py` — `MarketDataSource` / `NewsSource` ABCs (thin protocols). **(Phase 02)**
- `sources/yfinance_source.py` — `YFinanceSource(MarketDataSource)`: prices/%, indices, sector, commodities/rates (`^TNX ÷10`), earnings dates; `curl_cffi` impersonated session + retry/backoff/cache; `.info`/`.news` best-effort. **(Phase 02)**
- `sources/finnhub_source.py` — `FinnhubSource(NewsSource)`: company news + links, peers, market news, past earnings surprises; empty key ⇒ `[]`; 60/min spacing + per-run cache. **(Phase 02)**

### Additive core edits (Phase 03, backward-compatible)

- `src/agent_host/models.py` — add `InboundMessage.photo_file_ids: list[str] = []` (default empty keeps existing behavior).
- `src/agent_host/channels/telegram.py` — `parse_update` also builds a message for `message.photo` (largest size, caption→`text`); new `download_file(file_id) -> bytes`.
- `src/agent_host/llm.py` — new `complete_vision(messages, image_bytes, *, mime="image/png") -> str` using OpenRouter `image_url` data URI.
- `src/agent_host/host.py` — `_route` returns a helpful hint for an unknown leading-`/` command; a photo-only message routes to `config.image_agent`; assert command names unique across agents.
- `src/agent_host/config.py` — add `finnhub_api_key`, `vision_model`, `stock_max_tickers`, `stock_mover_threshold_pct`, `stock_max_movers`, `stock_peer_limit`, `stock_schedule_tz`, `image_agent`.

### Registration & infra (Phase 04)

- `src/agent_host/registry.py` — add `"stock": StockAgent` to `_agent_factories()`.
- Env / `.env` — add `stock` to `ENABLED_AGENTS`; set `FINNHUB_API_KEY`, `VISION_MODEL`, `STOCK_*`, `IMAGE_AGENT=stock`.
- AWS — new EventBridge schedule `agent-host-stock-recap`; Lambda package per the Phase 0 decision gate; Lambda console env vars.

### New test files (created alongside the phase that owns each module)

- `tests/test_stock_universe.py`, `tests/test_stock_watchlist.py`, `tests/test_stock_classify.py` — Phase 01.
- `tests/test_stock_calendar.py`, `tests/test_stock_sources.py`, `tests/test_stock_composer.py`, `tests/test_stock_agent.py` — Phase 02.
- `tests/test_stock_image_import.py`; extend `tests/test_telegram_channel.py`, `tests/test_host.py`, `tests/test_llm.py`, `tests/test_config.py` — Phase 03.
- Extend `tests/test_registry.py` — Phase 04.

---

## Cross-phase integration contract (shared-file ownership — READ BEFORE EXECUTING)

Several files are touched by more than one phase. To avoid one phase clobbering another's work, ownership is fixed here and **this section is authoritative** wherever a phase file's wording disagrees:

**`src/agent_host/config.py`** — additive fields, each added by exactly ONE phase:
- **Phase 01** adds ALL of: `finnhub_api_key`, `stock_max_tickers`, `stock_mover_threshold_pct`, `stock_max_movers`, `stock_peer_limit`, `stock_schedule_tz`, `image_agent`.
- **Phase 03** adds ONLY: `vision_model`.
- **Phase 02 does NOT touch `config.py`** (it consumes the fields Phase 01 already added). Ignore any "add finnhub_api_key / stock_* to config" step inside Phase 02 — those fields already exist.

**`src/agent_host/agents/stock/agent.py`** — created once, then extended:
- **Phase 01 CREATES it**: `StockAgent(Agent)` with `commands`, a text `handle_message` (`/tickers /add /remove /reset /help` + `/confirm`/`/cancel` **stubs**), a no-op `run_scheduled`, and `__init__(self, universe=None, ...)`.
- **Phase 02 MODIFIES it** (does NOT recreate): expand `__init__` to also accept the recap collaborators (`market, news, watchlist_factory, composer_factory, is_trading_day, today_fn`, all defaulted) while keeping `universe`; replace the no-op `run_scheduled` with the real recap pipeline; set `schedule`. **Keep Phase 01's `handle_message`, the `_cmd_*` helpers, `HELP`, and `_format_result` unchanged.**
- **Phase 03 MODIFIES it** again: in `handle_message`, add the photo branch (`msg.photo_file_ids` → `svc.channel.download_file` → `ImageImporter.import_photo`) and replace the `/confirm`/`/cancel` **stubs** with real `ImageImporter.confirm()`/`.cancel()` calls. (This is the image wiring — it lives in **Phase 03**, NOT Phase 04.)

**`tests/` for the agent** — split so no phase overwrites another's tests:
- **Phase 01** → `tests/test_stock_agent.py` (command behavior).
- **Phase 02** → `tests/test_stock_agent_recap.py` (`run_scheduled` recap).
- **Phase 03** → `tests/test_stock_agent_image.py` (photo routing + `/confirm`/`/cancel` wiring) and `tests/test_stock_image_import.py` (the `ImageImporter` unit).

**`src/agent_host/host.py`**, **`models.py`**, **`llm.py`**, **`registry.py`** — modified only in the phase noted: host/models/llm in **Phase 03**; registry in **Phase 04**.

**Module-to-phase map (corrects the roadmap table below):** `universe.py`, `watchlist.py` → Phase 01. `classify.py`, `calendar.py`, `composer.py`, `sources/*` → **Phase 02** (NOT Phase 01). `image_import.py` → Phase 03.

---

## Phase 0 — De-risk packaging (MANDATORY, do this BEFORE Phase 01 implementation)

**Why this gates everything:** `yfinance` transitively pulls in **pandas + numpy** (large native wheels) and **curl_cffi** (a CFFI native extension). This is the single biggest deployment risk (spec §15.1 / §16.2). If the packaged artifact exceeds Lambda's **250 MB unzipped** limit, or the native extensions were built for the wrong platform and `import yfinance` crashes at cold start, every downstream phase's cloud step is dead on arrival. We resolve fat-zip-vs-layer **now**, with a throwaway spike, so Phases 01–04 can be written against a known packaging model.

This is a **throwaway spike**: nothing here is committed to `src/`. All artifacts go under the scratchpad, and the branch stays clean. The deliverable is a written DECISION GATE recorded at the bottom of this section.

**Files:**
- Create (throwaway, scratchpad only — do NOT commit): a spike working dir, e.g. `/tmp/stock-pkg-spike/` (or the session scratchpad).
- Modify: none.
- Test: manual measurement + a Lambda smoke invoke (no pytest).

**Interfaces:**
- Consumes: nothing (pre-implementation spike).
- Produces: a **DECISION** value — `PACKAGING = "fat-zip"` or `PACKAGING = "numpy-pandas-layer"` — that Phase 04 (and any `yfinance` import assumption in Phases 01–02) reads.

- [ ] **Step 1: Build a platform-correct dependency package dir**

Build wheels for the Lambda runtime target (Amazon Linux 2 / manylinux2014, cp312, x86_64) — NOT the local macOS/arm64 wheels. The four native-ext flags are mandatory; without `--only-binary=:all:` pip may build a source dist against the wrong platform and the Lambda import will crash.

```bash
mkdir -p /tmp/stock-pkg-spike && cd /tmp/stock-pkg-spike
python3.12 -m pip install \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --target ./pkg \
  yfinance curl_cffi
```

Expected: pip resolves and downloads manylinux wheels for `yfinance`, `pandas`, `numpy`, `curl_cffi`, `cffi`, etc. into `./pkg` with **no** "Building wheel for ... (setup.py)" source-build lines (those would signal a wrong-platform fallback — investigate before proceeding).

- [ ] **Step 2: Measure unzipped size against the 250 MB limit and the 50 MB direct-zip limit**

```bash
cd /tmp/stock-pkg-spike
du -sh ./pkg
# also measure the zipped artifact for the direct-upload (50MB) path:
( cd ./pkg && zip -qr ../pkg.zip . ) && du -sh ./pkg.zip
```

Expected: record two numbers.
- `du -sh ./pkg` = **unzipped** size → compare to the **250 MB** Lambda unzipped ceiling (this is what matters for a layer or an S3-deployed zip).
- `du -sh ./pkg.zip` = **zipped** size → compare to the **50 MB** direct console/CLI upload ceiling (over 50 MB must go via S3 or a layer).

Rule of thumb from the spec: numpy+pandas+curl_cffi commonly land in the ~100–200 MB unzipped range — usually under 250 MB but comfortably over the 50 MB direct-zip path.

- [ ] **Step 3: Prove `import yfinance` actually loads on the Lambda runtime**

The size check is necessary but not sufficient — the native extensions must *import* on Amazon Linux. Verify with a throwaway Lambda smoke (preferred: it exercises the real runtime). Zip a tiny handler with the packaged deps and invoke it.

```bash
cd /tmp/stock-pkg-spike
cat > pkg/handler.py <<'PY'
def handler(event, context):
    import yfinance  # must not raise at import
    t = yfinance.Ticker("AAPL")  # construction only; network is fine to omit
    return {"ok": True, "yf": yfinance.__version__}
PY
( cd ./pkg && zip -qr ../smoke.zip . )
du -sh ./smoke.zip

# Deploy the throwaway function (S3 if smoke.zip > 50MB; otherwise --zip-file works).
# Throwaway name so it never collides with the real `agent-host` function:
aws lambda create-function \
  --region ca-central-1 \
  --function-name stock-pkg-smoke \
  --runtime python3.12 --architectures x86_64 \
  --handler handler.handler \
  --timeout 30 --memory-size 512 \
  --role "$LAMBDA_EXEC_ROLE_ARN" \
  --zip-file fileb://smoke.zip
aws lambda invoke --region ca-central-1 \
  --function-name stock-pkg-smoke /tmp/stock-pkg-spike/out.json
cat /tmp/stock-pkg-spike/out.json
```

Expected: `out.json` contains `{"ok": true, "yf": "<version>"}` and NO `Runtime.ImportModuleError` / `errorMessage`. If it errors with a `.so` / GLIBC / `numpy` import failure, the wheels are wrong-platform — re-check Step 1's flags before proceeding. (If AWS access is not yet available in the local environment, fall back to a Docker check: `docker run --rm -v /tmp/stock-pkg-spike/pkg:/var/task:ro public.ecr.aws/lambda/python:3.12 python -c "import yfinance; print(yfinance.__version__)"` — the same import assertion on the same runtime image.)

- [ ] **Step 4: Tear down the throwaway function and record the DECISION GATE**

```bash
aws lambda delete-function --region ca-central-1 --function-name stock-pkg-smoke
rm -rf /tmp/stock-pkg-spike
```

Then record the decision (this is the gate Phase 04 reads):

**DECISION GATE — packaging model**

- **If** `du -sh ./pkg` (unzipped) is **≤ ~200 MB** AND the smoke import passed AND the rest of the app fits under **250 MB unzipped** total → **`PACKAGING = "fat-zip"`**: ship one deployment artifact (via S3, since it exceeds the 50 MB direct-zip limit). Downstream assumes `yfinance` is importable in the function's own package; no layer ARN needed.
- **Else (near/over 250 MB, or you want faster deploys / to share the heavy deps)** → **`PACKAGING = "numpy-pandas-layer"`**: put `numpy`+`pandas`(+`curl_cffi`) in a **Lambda layer** built with the same four flags, keep the function zip small (app code + `yfinance` + light deps). Downstream (Phase 04) attaches the layer ARN to `agent-host` and the function zip stays under the direct-upload limit.

**What each downstream phase assumes from this gate:**
- **Phases 01–02** assume `import yfinance` succeeds in the Lambda runtime (proven here) and therefore may import it at module load in `classify.py` / `sources/yfinance_source.py`; local tests still mock/fake all network and inject sources, so the import path is never exercised against the network in CI.
- **Phase 04** reads `PACKAGING` to choose the deploy recipe: fat-zip-via-S3 vs function-zip-plus-layer. It does not re-run this spike; it trusts the recorded decision.

- [ ] **Step 5: No commit (throwaway spike)**

There is nothing to commit — the spike dir is deleted and `src/` is untouched. Confirm the branch is clean:

```bash
git status --porcelain
```

Expected: empty output (no changes). Paste the two size numbers and the chosen `PACKAGING` value into the phase-04 file's opening note before starting Phase 04.

---

## Phase roadmap (execution order & handoffs)

1. **Phase 01 — Watchlist guardrail** (`2026-07-29-stock-agent-01-watchlist-guardrail.md`)
   The security showcase and the foundation everything else stands on. Builds `universe.py` (ground-truth allowlist), `watchlist.py` (sanitize → crypto-reason → shape → allowlist → cap/dedupe pipeline, `validate_candidates`, `WatchlistManager`), and `classify.py`. Ships the full attack-catalog test suite (§6.1): injection strings, LeetCode paste, fake/delisted/garbage symbols, homoglyph/zero-width/bidi, 500-symbol flood, formula/XSS payloads all rejected; **crypto rejected with the explicit "crypto not supported" reason**; valid tickers pass; 50-cap enforced. Independently testable with a fixture universe, zero network.

2. **Phase 02 — Recap** (`2026-07-29-stock-agent-02-recap.md`)
   The daily digest. Builds `calendar.py` (XNYS gate), `sources/` (`YFinanceSource`, `FinnhubSource` with graceful empty-key degradation + `^TNX ÷10` + per-source try/except), `composer.py` (`RecapData` → Telegram-HTML, omit empty sections, escape), and `StockAgent` `run_scheduled` + the `/tickers /add /remove /reset /help /confirm /cancel` commands. **Text `/add` REUSES Phase 01's `validate_candidates`** — no new validator. Holiday/weekend → sends nothing. Independently testable with fakes + `dry_run` channel.

3. **Phase 03 — Image import** (`2026-07-29-stock-agent-03-image-import.md`)
   The screenshot showcase + the additive core edits. Adds `InboundMessage.photo_file_ids`, `TelegramChannel` photo parse + `download_file`, `LLMClient.complete_vision`, and `Host` photo-routing (`config.image_agent`) + unknown-command hint + unique-command assertion — all backward-compatible (existing tests stay green). Wires the quarantined vision extractor → schema-lock → **the same Phase 01 `validate_candidates`** → PII discard → `/confirm` human gate. Independently testable with a fake vision client returning injected instructions + PII; only valid tickers survive, nothing stored/echoed.

4. **Phase 04 — Deploy** (`2026-07-29-stock-agent-04-deploy.md`)
   Registration + cloud. Adds `"stock": StockAgent` to the registry, `stock` to `ENABLED_AGENTS`, all new `Config`/env values; packages per the **Phase 0 DECISION GATE**; creates the `agent-host-stock-recap` EventBridge schedule (`cron(0 16 ? * MON-FRI *)` `America/Vancouver`, payload `{"mode":"scheduled","agent":"stock"}`); sets Lambda console env (`FINNHUB_API_KEY`, `VISION_MODEL`, `STOCK_*`, `IMAGE_AGENT`); verifies via `/tickers`, a manual scheduled invoke, and `aws logs tail /aws/lambda/agent-host`.

**Reuse contract across phases:** `validate_candidates(raw, universe, *, max_tickers)` from Phase 01 is the *only* ingestion gate. Phase 02 (text `/add`) and Phase 03 (image import) both call it verbatim — the LLM/vision extractor merely produces `raw` candidate strings; the deterministic allowlist check is what decides membership. This is the property the whole design hangs on (spec §6.2/§6.3), so it is built and hardened once, first.

---

## Execution note

- **Local-first, always.** Every phase must be fully green under `STORE_BACKEND=sqlite` with all network mocked/faked (injected sources, fake LLM/vision clients, fixture universe/symbol files, `dry_run` channel) **before** any cloud step. The only phase that touches AWS is Phase 04, and it only runs after 0–03 are locally green.
- **Design moved fast; execution is strict TDD.** Planning and design are done. Implementation is test-first for every task: write the failing test, run it and see it fail for the stated reason, write the minimal code, run it and see it pass, commit with a conventional-commit message. No implementation code before its test.
- **Review checkpoints between tasks.** Per the required sub-skill (subagent-driven-development recommended), each task ends with an independently-testable deliverable and a review gate before the next task begins.
- **Local run harness:** `python -m agent_host.entrypoints.local_run run stock` exercises `run_scheduled` end-to-end once the agent is registered (Phase 04) — but earlier phases test the pieces directly with pytest, no registration required.

---

**Phase count in this file: 1 (Phase 0 — packaging de-risk, 5 steps). This file also indexes the 4 implementation phases (01–04) carried in sibling plan files.**
